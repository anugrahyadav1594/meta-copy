"""Start a throwaway MetaScale API, run a command against it, then stop it.

This is what makes ``make demo`` (and ``make demo-full``) a single command:

    python scripts/demo_server.py --port 8000 --seed small --wait 25 -- \\
        python scripts/demo.py --base-url http://localhost:8000

The API is started as a child process with the environment the caller set
(MODE, CACHE_ENABLED, ...), we poll ``/health`` until it answers (or the wait
budget runs out), then the command runs with the live API. The server is always
terminated on the way out, success or failure.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def wait_for_health(url: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:  # noqa: S310
                if response.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.4)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--seed", default="small")
    parser.add_argument("--wait", type=float, default=30.0, help="seconds to wait for /health")
    parser.add_argument("--sharded", action="store_true", default=True)
    parser.add_argument("--no-sharded", dest="sharded", action="store_false")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = [part for part in args.command if part != "--"]
    if not command:
        print("nothing to run: pass a command after --", file=sys.stderr)
        return 2

    server_cmd = [
        sys.executable,
        str(ROOT / "scripts" / "dev_server.py"),
        "--port",
        str(args.port),
        "--seed",
        args.seed,
    ]
    if args.sharded:
        server_cmd += ["--sharded", "--sharded-seed"]

    env = {**os.environ, "PYTHONPATH": os.environ.get("PYTHONPATH", "")}
    print(f"[demo_server] starting API on port {args.port}: {' '.join(server_cmd)}")
    server = subprocess.Popen(server_cmd, cwd=str(ROOT), env=env)
    exit_code = 1
    try:
        health_url = f"http://127.0.0.1:{args.port}/health"
        if not wait_for_health(health_url, args.wait):
            print(f"[demo_server] API never became healthy at {health_url}", file=sys.stderr)
            return 1
        print(f"[demo_server] API is healthy — running: {' '.join(command)}")
        exit_code = subprocess.call(command, cwd=str(ROOT), env=env)
    finally:
        print("[demo_server] stopping API")
        server.send_signal(signal.SIGTERM)
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
