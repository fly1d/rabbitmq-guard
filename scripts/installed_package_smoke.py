#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path


def run(command, cwd, env):
    return subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: installed_package_smoke.py DIST_WHEEL")
    wheel = Path(sys.argv[1]).resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise SystemExit("wheel not found: {}".format(wheel))

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment["RABBITMQ_GUARD_DATA_DIR"] = str(root / "customer-data")
        virtualenv = root / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(virtualenv)], check=True)
        python = virtualenv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                str(wheel),
            ],
            root,
            environment,
        )

        default_path_checker = textwrap.dedent(
            """
            from pathlib import Path

            import rabbitmq_guard
            from rabbitmq_guard.cli import DEFAULT_DATABASE

            package_root = Path(rabbitmq_guard.__file__).resolve().parent
            assert DEFAULT_DATABASE.is_absolute()
            assert DEFAULT_DATABASE.name == "rabbitmq-guard.db"
            assert DEFAULT_DATABASE.is_relative_to(Path.home())
            assert package_root not in DEFAULT_DATABASE.parents
            """
        )
        default_environment = dict(environment)
        default_environment.pop("RABBITMQ_GUARD_DATA_DIR", None)
        run([str(python), "-c", default_path_checker], root, default_environment)

        cases = run([str(python), "-m", "rabbitmq_guard", "cases"], root, environment)
        if len([line for line in cases.splitlines() if line.strip()]) != 11:
            raise SystemExit("installed package did not expose all 11 scenarios")

        result = json.loads(
            run(
                [
                    str(python),
                    "-m",
                    "rabbitmq_guard",
                    "demo",
                    "memory_alarm",
                    "--format",
                    "json",
                ],
                root,
                environment,
            )
        )
        if result["findings"][0]["rule_id"] != "node.memory_alarm":
            raise SystemExit("installed demo returned the wrong diagnostic result")

        checker = textwrap.dedent(
            """
            import json
            import threading
            from pathlib import Path
            from urllib.request import urlopen

            from rabbitmq_guard import __version__
            from rabbitmq_guard.cli import DEFAULT_CASE_DIR, DEFAULT_DATABASE
            from rabbitmq_guard.webapp import create_server

            server, _ = create_server(
                "127.0.0.1", 0, DEFAULT_DATABASE, DEFAULT_CASE_DIR, False
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = "http://127.0.0.1:{}".format(server.server_address[1])
                with urlopen(base_url + "/api/health", timeout=3) as response:
                    health = json.loads(response.read().decode("utf-8"))
                with urlopen(base_url + "/api/scenarios", timeout=3) as response:
                    scenarios = json.loads(response.read().decode("utf-8"))
                assert health == {"ok": True, "version": __version__, "live_enabled": False}
                assert len(scenarios["scenarios"]) == 11
                assert DEFAULT_DATABASE.parent == Path(__import__("os").environ["RABBITMQ_GUARD_DATA_DIR"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
            """
        )
        run([str(python), "-c", checker], root, environment)
    print("installed package smoke test passed")


if __name__ == "__main__":
    main()
