import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .collector import CollectionError, ManagementApiCollector
from .delivery import verify_delivery_bundle, write_delivery_bundle
from .diagnostics import diagnose, render_json, render_markdown, render_text
from .generator import generate_variants, load_cases, write_jsonl
from .sanitizer import DEFAULT_KEY_ENV, redaction_key_from_env, sanitize_snapshot
from .webapp import serve


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASE_DIR = PROJECT_ROOT / "data" / "scenarios"
DEFAULT_DATABASE = PROJECT_ROOT / "var" / "rabbitmq-guard.db"


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("输入必须是 JSON object")
    return value


def _write_output(content: str, output: Optional[Path]) -> None:
    if output:
        output.write_text(content, encoding="utf-8")
        print("已写入 {}".format(output))
    else:
        print(content)


def _render(findings: Any, output_format: str) -> str:
    if output_format == "json":
        return render_json(findings)
    if output_format == "markdown":
        return render_markdown(findings)
    return render_text(findings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rabbitmq-guard",
        description="基于证据的 RabbitMQ 只读诊断 MVP",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    cases_parser = subparsers.add_parser("cases", help="列出内置合成案例")
    cases_parser.add_argument("--case-dir", type=Path, default=DEFAULT_CASE_DIR)

    diagnose_parser = subparsers.add_parser("diagnose", help="诊断一个标准化 JSON 快照")
    diagnose_parser.add_argument("snapshot", type=Path)
    diagnose_parser.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    diagnose_parser.add_argument("--output", type=Path)

    collect_parser = subparsers.add_parser("collect", help="从 Management API 只读采集快照")
    collect_parser.add_argument("--url", default="http://localhost:15672")
    collect_parser.add_argument("--username", default="guest")
    collect_parser.add_argument("--password-env", default="RABBITMQ_PASSWORD")
    collect_parser.add_argument("--output", type=Path, required=True)
    collect_parser.add_argument(
        "--sanitize",
        action="store_true",
        help="使用环境变量中的密钥对输出快照做稳定伪名脱敏",
    )
    collect_parser.add_argument("--redaction-key-env", default=DEFAULT_KEY_ENV)

    live_parser = subparsers.add_parser("live", help="采集并立即诊断，不保存密码")
    live_parser.add_argument("--url", default="http://localhost:15672")
    live_parser.add_argument("--username", default="guest")
    live_parser.add_argument("--password-env", default="RABBITMQ_PASSWORD")
    live_parser.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    live_parser.add_argument("--output", type=Path)

    generate_parser = subparsers.add_parser("generate", help="从基准案例生成带标签的 JSONL 变体")
    generate_parser.add_argument("--case-dir", type=Path, default=DEFAULT_CASE_DIR)
    generate_parser.add_argument("--count-per-case", type=int, default=20)
    generate_parser.add_argument("--seed", type=int, default=20260811)
    generate_parser.add_argument("--output", type=Path, required=True)

    sanitize_parser = subparsers.add_parser("sanitize", help="脱敏一个标准化 JSON 快照")
    sanitize_parser.add_argument("snapshot", type=Path)
    sanitize_parser.add_argument("--output", type=Path, required=True)
    sanitize_parser.add_argument("--redaction-key-env", default=DEFAULT_KEY_ENV)

    deliver_parser = subparsers.add_parser(
        "deliver", help="生成可校验的客户脱敏诊断交付包"
    )
    source_group = deliver_parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--snapshot", type=Path)
    source_group.add_argument("--url")
    deliver_parser.add_argument("--username", default="guest")
    deliver_parser.add_argument("--password-env", default="RABBITMQ_PASSWORD")
    deliver_parser.add_argument("--redaction-key-env", default=DEFAULT_KEY_ENV)
    deliver_parser.add_argument("--output", type=Path, required=True)

    verify_parser = subparsers.add_parser(
        "verify-delivery", help="校验客户交付包的完整性和内部一致性"
    )
    verify_parser.add_argument("bundle", type=Path)

    serve_parser = subparsers.add_parser("serve", help="启动本地诊断工作台")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8787)
    serve_parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    serve_parser.add_argument("--case-dir", type=Path, default=DEFAULT_CASE_DIR)
    serve_parser.add_argument(
        "--enable-live",
        action="store_true",
        help="允许工作台使用请求中的临时凭据连接 Management API",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "cases":
            for case in load_cases(args.case_dir):
                scenario = case.get("scenario") or {}
                print(
                    "{:<28} {:<18} {}".format(
                        scenario.get("id", "unknown"),
                        scenario.get("kind", "unknown"),
                        scenario.get("title", ""),
                    )
                )
            return

        if args.command == "diagnose":
            findings = diagnose(_load_json(args.snapshot))
            _write_output(_render(findings, args.format), args.output)
            return

        if args.command in {"collect", "live"}:
            redaction_key = None
            if args.command == "collect" and args.sanitize:
                redaction_key = redaction_key_from_env(args.redaction_key_env)
            collector = ManagementApiCollector.from_env(
                args.url, args.username, args.password_env
            )
            snapshot = collector.collect()
            if args.command == "collect":
                if args.sanitize:
                    if redaction_key is None:
                        raise ValueError("脱敏密钥未加载")
                    snapshot = sanitize_snapshot(snapshot, redaction_key)
                args.output.write_text(
                    json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                message = "已采集并脱敏快照到" if args.sanitize else "已采集只读快照到"
                print("{} {}".format(message, args.output))
            else:
                _write_output(_render(diagnose(snapshot), args.format), args.output)
            return

        if args.command == "sanitize":
            sanitized = sanitize_snapshot(
                _load_json(args.snapshot), redaction_key_from_env(args.redaction_key_env)
            )
            args.output.write_text(
                json.dumps(sanitized, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print("已写入脱敏快照到 {}".format(args.output))
            return

        if args.command == "deliver":
            if args.output.exists():
                raise ValueError("输出文件已存在，不会覆盖: {}".format(args.output))
            redaction_key = redaction_key_from_env(args.redaction_key_env)
            if args.snapshot is not None:
                snapshot = _load_json(args.snapshot)
            else:
                collector = ManagementApiCollector.from_env(
                    args.url, args.username, args.password_env
                )
                snapshot = collector.collect()
            verification = write_delivery_bundle(snapshot, redaction_key, args.output)
            print(
                "已生成并校验客户交付包到 {}（{} 项诊断结果）\nSHA-256: {}".format(
                    args.output,
                    verification["summary"]["total"],
                    verification["bundle_sha256"],
                )
            )
            return

        if args.command == "verify-delivery":
            verification = verify_delivery_bundle(args.bundle)
            print(
                "交付包校验通过：{} · {} 项诊断结果 · 生成器 v{}\nSHA-256: {}".format(
                    verification["cluster_name"],
                    verification["summary"]["total"],
                    verification["generator_version"] or "unknown",
                    verification["bundle_sha256"],
                )
            )
            return

        if args.command == "generate":
            if args.count_per_case < 1:
                parser.error("--count-per-case 必须大于 0")
            variants = generate_variants(
                load_cases(args.case_dir), args.count_per_case, args.seed
            )
            count = write_jsonl(args.output, variants)
            print("已生成 {} 条带标签合成样本到 {}".format(count, args.output))
            return

        if args.command == "serve":
            serve(args.host, args.port, args.database, args.case_dir, args.enable_live)
            return
    except (CollectionError, OSError, ValueError, json.JSONDecodeError) as exc:
        print("错误: {}".format(exc), file=sys.stderr)
        raise SystemExit(2)
