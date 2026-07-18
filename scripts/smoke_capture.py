from __future__ import annotations

import asyncio
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.audio.bridge_client import ProcessBridgeClient


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-id", default="com.google.Chrome")
    parser.add_argument("--duration-ms", type=int, default=1500)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["swift", "build", "--package-path", "apps/macos-bridge"], cwd=root, check=True)
    executable = root / "apps/macos-bridge/.build/debug/robin-macos-bridge"
    client = ProcessBridgeClient(executable)
    apps = await client.list_capture_applications()
    app_lines = apps.result.get("applications", "").splitlines()
    target_visible = any(line.startswith(f"{args.bundle_id}:") for line in app_lines)
    output = root / "RobinWorkspace/sessions/capture-smoke.wav"
    result = await client.capture_audio_sample(args.bundle_id, output, duration_ms=args.duration_ms)
    if result.ok:
        print(
            "Capture smoke passed: "
            f"bundle_id={args.bundle_id} "
            f"target_visible={target_visible} "
            f"path={result.result.get('path')} "
            f"samples={result.result.get('samples')} "
            f"bytes={result.result.get('bytes')}"
        )
    else:
        print(
            "Capture smoke not ready: "
            f"bundle_id={args.bundle_id} "
            f"target_visible={target_visible} "
            f"error={result.error} "
            f"visible_apps={app_lines[:8]}"
        )


if __name__ == "__main__":
    asyncio.run(main())
