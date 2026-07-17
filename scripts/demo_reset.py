from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seed_demo_workspace import main as seed_demo_workspace


ROOT = Path("RobinWorkspace")
CORE_HEALTH = "http://127.0.0.1:8787/health"
WEB_HEALTH = "http://127.0.0.1:3000"


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset Robin to a clean demo workspace.")
    parser.add_argument("--start", action="store_true", help="Start the Robin supervisor after reset.")
    parser.add_argument("--skip-stop", action="store_true", help="Do not stop services on Robin ports.")
    parser.add_argument("--archive-root", default=str(ROOT / "archive"))
    args = parser.parse_args()

    if not args.skip_stop:
        stop_robin_processes()
    archive_path = archive_demo_state(Path(args.archive_root))
    seed_demo_workspace()
    if args.start:
        start_supervisor()
        wait_for_http(CORE_HEALTH, "robin-core")
        wait_for_http(WEB_HEALTH, "robin-web")
    print(f"Demo reset complete. Archived previous state at {archive_path}")


def stop_robin_processes() -> None:
    pids = robin_port_pids()
    candidates = set(pids)
    for pid in pids:
        candidates.update(ancestor_pids(pid))
    targets = sorted(pid for pid in candidates if is_robin_process(pid))
    for pid in targets:
        terminate(pid)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        remaining = [pid for pid in targets if process_exists(pid)]
        if not remaining:
            break
        time.sleep(0.2)
    for pid in targets:
        if process_exists(pid):
            kill(pid)


def robin_port_pids() -> set[int]:
    pids: set[int] = set()
    for port in (8787, 3000):
        try:
            result = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}"],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            continue
        for line in result.stdout.splitlines():
            if line.strip().isdigit():
                pids.add(int(line.strip()))
    return pids


def parent_pid(pid: int) -> int | None:
    result = subprocess.run(["ps", "-p", str(pid), "-o", "ppid="], check=False, capture_output=True, text=True)
    value = result.stdout.strip()
    return int(value) if value.isdigit() else None


def ancestor_pids(pid: int) -> set[int]:
    ancestors: set[int] = set()
    current = pid
    while True:
        parent = parent_pid(current)
        if not parent or parent <= 1 or parent in ancestors:
            return ancestors
        ancestors.add(parent)
        current = parent


def is_robin_process(pid: int) -> bool:
    result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], check=False, capture_output=True, text=True)
    command = result.stdout
    markers = [
        "scripts/robin.py dev",
        "robin_core.main:app",
        "apps/web dev",
        "next dev",
    ]
    return any(marker in command for marker in markers)


def terminate(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def kill(pid: int) -> None:
    subprocess.run(["kill", "-KILL", str(pid)], check=False, capture_output=True)


def process_exists(pid: int) -> bool:
    return subprocess.run(["kill", "-0", str(pid)], check=False, capture_output=True).returncode == 0


def archive_demo_state(archive_root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_path = archive_root / stamp
    archive_path.mkdir(parents=True, exist_ok=True)
    for name in ("generated", "sessions"):
        path = ROOT / name
        if path.exists():
            shutil.move(str(path), str(archive_path / name))
    db_path = ROOT / "robin.db"
    if db_path.exists():
        shutil.move(str(db_path), str(archive_path / "robin.db"))
    (ROOT / "generated").mkdir(parents=True, exist_ok=True)
    (ROOT / "sessions").mkdir(parents=True, exist_ok=True)
    (ROOT / "cache").mkdir(parents=True, exist_ok=True)
    return archive_path.resolve()


def start_supervisor() -> None:
    log_dir = ROOT / "sessions" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "demo-reset-supervisor.log"
    log = log_path.open("ab")
    subprocess.Popen(
        ["uv", "run", "python", "scripts/robin.py", "dev"],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log.close()
    print(f"Started Robin supervisor; log={log_path}")


def wait_for_http(url: str, name: str, timeout_s: float = 30) -> None:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    print(f"healthy {name}: {url}")
                    return
        except URLError as exc:
            last_error = exc
        time.sleep(0.5)
    raise SystemExit(f"Timed out waiting for {name} at {url}: {last_error}")


if __name__ == "__main__":
    main()
