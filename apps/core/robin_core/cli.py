from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .config import load_settings
from .preflight import run_preflight
from .runtime import RobinRuntime
from .supervisor import Supervisor, default_processes


async def doctor() -> None:
    runtime = RobinRuntime(load_settings())
    runtime.refresh_health()
    for item in runtime.health + run_preflight(runtime.settings):
        state = "ok" if item.ok else "fail"
        print(f"{state:4} {item.name}: {item.detail}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="robin-core")
    parser.add_argument("command", choices=["doctor", "dev"])
    parser.add_argument("--log-dir", default="RobinWorkspace/sessions/logs")
    args = parser.parse_args()
    if args.command == "doctor":
        asyncio.run(doctor())
    elif args.command == "dev":
        supervisor = Supervisor(default_processes(), log_dir=Path(args.log_dir))
        asyncio.run(supervisor.run())


if __name__ == "__main__":
    main()
