import json
import mimetypes
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import __version__
from .collector import CollectionError, ManagementApiCollector
from .comparison import compare_runs, render_comparison_markdown
from .diagnostics import diagnose, render_markdown
from .generator import load_cases
from .models import Finding
from .storage import RunStore


MAX_REQUEST_BYTES = 5 * 1024 * 1024
WEB_ROOT = Path(__file__).resolve().parent / "web"


@dataclass
class AppContext:
    store: RunStore
    case_dir: Path
    live_enabled: bool = False

    def cases(self) -> Dict[str, Dict[str, Any]]:
        result = {}
        for case in load_cases(self.case_dir):
            scenario = case.get("scenario") or {}
            case_id = str(scenario.get("id", "unknown"))
            result[case_id] = case
        return result


def _summary(findings: Any) -> Dict[str, Any]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for finding in findings:
        severity = finding.severity if isinstance(finding, Finding) else finding["severity"]
        counts[severity] += 1
    if counts["critical"]:
        status = "critical"
    elif counts["high"]:
        status = "high"
    elif counts["medium"] or counts["low"]:
        status = "attention"
    else:
        status = "healthy"
    return {"status": status, "counts": counts, "total": sum(counts.values())}


def _build_result(
    snapshot: Dict[str, Any], findings: Any, run: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    cluster = snapshot.get("cluster") or {}
    capture = snapshot.get("capture") or {}
    return {
        "run": run,
        "summary": _summary(findings),
        "cluster": {
            "name": cluster.get("name", "unknown"),
            "connections": cluster.get("connections", 0),
            "channels": cluster.get("channels", 0),
            "queues": cluster.get("queues", len(snapshot.get("queues") or [])),
            "consumers": cluster.get("consumers", 0),
        },
        "capture": capture,
        "findings": [
            finding.to_dict() if isinstance(finding, Finding) else finding
            for finding in findings
        ],
    }


def _validate_snapshot(snapshot: Any) -> Dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ValueError("快照必须是 JSON object")
    if not isinstance(snapshot.get("cluster"), dict):
        raise ValueError("快照缺少 cluster object")
    for key in ("nodes", "queues", "connections"):
        if key in snapshot and not isinstance(snapshot[key], list):
            raise ValueError("{} 必须是 array".format(key))
    return snapshot


def _findings_from_run(run: Dict[str, Any]) -> Any:
    return [Finding(**finding) for finding in run["findings"]]


def make_handler(context: AppContext):
    class GuardHandler(BaseHTTPRequestHandler):
        server_version = "RabbitMQGuard/{}".format(__version__)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
            content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def _send_error(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
            self._send_json({"error": message}, status)

        def _read_json(self) -> Dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("Content-Length 无效") from exc
            if length <= 0:
                raise ValueError("请求内容为空")
            if length > MAX_REQUEST_BYTES:
                raise ValueError("请求超过 5MB 限制")
            raw = self.rfile.read(length)
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("请求必须是 JSON object")
            return value

        def _serve_asset(self, request_path: str) -> None:
            relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
            candidate = (WEB_ROOT / relative).resolve()
            if WEB_ROOT.resolve() not in candidate.parents and candidate != WEB_ROOT.resolve():
                self._send_error("not found", HTTPStatus.NOT_FOUND)
                return
            if not candidate.is_file():
                self._send_error("not found", HTTPStatus.NOT_FOUND)
                return
            content = candidate.read_bytes()
            content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") else ""))
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/health":
                self._send_json({"ok": True, "version": __version__, "live_enabled": context.live_enabled})
                return
            if path == "/api/scenarios":
                scenarios = []
                for case in context.cases().values():
                    scenario = case.get("scenario") or {}
                    scenarios.append(
                        {
                            "id": scenario.get("id"),
                            "title": scenario.get("title"),
                            "description": scenario.get("description"),
                            "expected_findings": scenario.get("expected_findings") or [],
                        }
                    )
                self._send_json({"scenarios": scenarios})
                return
            if path == "/api/runs":
                try:
                    limit = int((parse_qs(parsed.query).get("limit") or ["30"])[0])
                except ValueError:
                    limit = 30
                self._send_json({"runs": context.store.list(limit)})
                return
            if path.startswith("/api/compare/"):
                parts = path[len("/api/compare/") :].split("/")
                wants_report = len(parts) == 3 and parts[2] == "report.md"
                if len(parts) not in {2, 3} or (len(parts) == 3 and not wants_report):
                    self._send_error("对比路径无效", HTTPStatus.NOT_FOUND)
                    return
                baseline = context.store.get(parts[0])
                current = context.store.get(parts[1])
                if baseline is None or current is None:
                    self._send_error("诊断记录不存在", HTTPStatus.NOT_FOUND)
                    return
                try:
                    comparison = compare_runs(baseline, current)
                except ValueError as exc:
                    self._send_error(str(exc))
                    return
                if wants_report:
                    report = render_comparison_markdown(comparison).encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/markdown; charset=utf-8")
                    self.send_header(
                        "Content-Disposition",
                        'attachment; filename="rabbitmq-guard-comparison-{}-{}.md"'.format(
                            parts[0], parts[1]
                        ),
                    )
                    self.send_header("Content-Length", str(len(report)))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(report)
                    return
                self._send_json(comparison)
                return
            if path.startswith("/api/runs/"):
                suffix = path[len("/api/runs/") :]
                wants_report = suffix.endswith("/report.md")
                run_id = suffix[: -len("/report.md")] if wants_report else suffix
                run = context.store.get(run_id)
                if run is None:
                    self._send_error("诊断记录不存在", HTTPStatus.NOT_FOUND)
                    return
                if wants_report:
                    report = render_markdown(_findings_from_run(run), run).encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/markdown; charset=utf-8")
                    self.send_header("Content-Disposition", 'attachment; filename="rabbitmq-guard-{}.md"'.format(run_id))
                    self.send_header("Content-Length", str(len(report)))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(report)
                    return
                self._send_json(_build_result(run["snapshot"], run["findings"], run))
                return
            self._serve_asset(path)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                payload = self._read_json()
                if parsed.path == "/api/analyze/scenario":
                    case_id = str(payload.get("id", ""))
                    case = context.cases().get(case_id)
                    if case is None:
                        self._send_error("演示案例不存在", HTTPStatus.NOT_FOUND)
                        return
                    self._analyze(
                        case,
                        label=str((case.get("scenario") or {}).get("title", case_id)),
                        source_kind="scenario",
                        persist=bool(payload.get("persist", True)),
                    )
                    return
                if parsed.path == "/api/analyze/snapshot":
                    snapshot = _validate_snapshot(payload.get("snapshot"))
                    label = str(payload.get("label") or "上传快照")
                    self._analyze(snapshot, label, "upload", True)
                    return
                if parsed.path == "/api/analyze/live":
                    if not context.live_enabled:
                        self._send_error("实时连接未启用", HTTPStatus.FORBIDDEN)
                        return
                    url = str(payload.get("url", "")).strip()
                    username = str(payload.get("username", "")).strip()
                    password = str(payload.get("password", ""))
                    if not url.startswith(("http://", "https://")):
                        raise ValueError("Management API URL 必须使用 http 或 https")
                    if not username or not password:
                        raise ValueError("用户名和密码不能为空")
                    snapshot = ManagementApiCollector(url, username, password).collect()
                    self._analyze(snapshot, "实时诊断 · {}".format(url), "live", True)
                    return
                self._send_error("not found", HTTPStatus.NOT_FOUND)
            except (CollectionError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                self._send_error(str(exc))
            except Exception:
                self._send_error("服务器处理失败", HTTPStatus.INTERNAL_SERVER_ERROR)

        def _analyze(
            self,
            snapshot: Dict[str, Any],
            label: str,
            source_kind: str,
            persist: bool,
        ) -> None:
            findings = diagnose(snapshot)
            run = context.store.save(snapshot, findings, label, source_kind) if persist else None
            self._send_json(_build_result(snapshot, findings, run))

    return GuardHandler


def create_server(
    host: str,
    port: int,
    database: Path,
    case_dir: Path,
    live_enabled: bool,
) -> Tuple[ThreadingHTTPServer, AppContext]:
    if live_enabled and host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("实时连接只能在回环地址上启用")
    context = AppContext(RunStore(database), case_dir, live_enabled)
    server = ThreadingHTTPServer((host, port), make_handler(context))
    return server, context


def serve(
    host: str,
    port: int,
    database: Path,
    case_dir: Path,
    live_enabled: bool,
) -> None:
    server, _ = create_server(host, port, database, case_dir, live_enabled)
    print("RabbitMQ Guard: http://{}:{}".format(host, server.server_address[1]))
    if live_enabled:
        print("实时连接已启用；密码仅用于当次请求，不写入数据库。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
