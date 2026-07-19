from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class EvalResult:
    name: str
    command: list[str]
    ok: bool
    duration_seconds: float
    output_tail: str


DETERMINISTIC = [
    ("core_unit_integration_adversarial", ["uv", "run", "pytest", "-q"]),
    ("dashboard_tests", ["pnpm", "--dir", "apps/web", "test"]),
    ("dashboard_types", ["pnpm", "--dir", "apps/web", "typecheck"]),
    ("dashboard_build", ["pnpm", "--dir", "apps/web", "build"]),
    ("native_bridge_build", ["swift", "build", "--package-path", "apps/macos-bridge"]),
    ("meet_fixture", ["uv", "run", "python", "scripts/smoke_meet_fixture.py"]),
    ("meet_recovery", ["uv", "run", "python", "scripts/smoke_meet_recovery.py"]),
    ("leave_cleanup", ["uv", "run", "python", "scripts/smoke_leave_cleanup.py"]),
    (
        "conversation_revision_q_and_a",
        ["uv", "run", "python", "scripts/smoke_conversation_revision.py"],
    ),
    ("retry_and_narration", ["uv", "run", "python", "scripts/smoke_retry_present.py"]),
    ("clarification", ["uv", "run", "python", "scripts/smoke_clarification.py"]),
    ("queue_and_cancellation", ["uv", "run", "python", "scripts/smoke_queue.py"]),
    ("deduplication", ["uv", "run", "python", "scripts/smoke_dedup.py"]),
    ("artifact_validation", ["uv", "run", "python", "scripts/smoke_validation.py"]),
    ("observability", ["uv", "run", "python", "scripts/smoke_observability.py"]),
    ("resource_budgets", ["uv", "run", "python", "scripts/smoke_resource_budgets.py"]),
]

LIVE_MODELS = [
    (
        "general_agent",
        ["env", "PYTHONPATH=apps/core", "uv", "run", "python", "scripts/smoke_general_agent.py"],
    ),
    ("browser_operator", ["uv", "run", "python", "scripts/smoke_browser_operator.py"]),
    ("meeting_memory", ["uv", "run", "python", "scripts/smoke_meeting_memory.py"]),
    ("realtime_audio", ["uv", "run", "python", "scripts/smoke_realtime_audio.py"]),
    ("audio_workflow", ["uv", "run", "python", "scripts/smoke_audio_workflow.py"]),
]


def run(name: str, command: list[str]) -> EvalResult:
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    output = (completed.stdout + "\n" + completed.stderr).strip()
    return EvalResult(
        name=name,
        command=command,
        ok=completed.returncode == 0,
        duration_seconds=round(time.monotonic() - started, 3),
        output_tail=output[-4000:],
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Robin's outcome-based operator evaluation matrix."
    )
    parser.add_argument(
        "--live-models",
        action="store_true",
        help="Also run API-backed agent, browser, memory, and realtime-audio evaluations.",
    )
    parser.add_argument(
        "--live-only",
        action="store_true",
        help="Run only API-backed agent, browser, memory, and realtime-audio evaluations.",
    )
    args = parser.parse_args()
    commands = (
        LIVE_MODELS if args.live_only else DETERMINISTIC + (LIVE_MODELS if args.live_models else [])
    )
    results: list[EvalResult] = []
    for index, (name, command) in enumerate(commands, start=1):
        print(f"[{index}/{len(commands)}] {name}: {' '.join(command)}", flush=True)
        result = run(name, command)
        results.append(result)
        print(
            f"  {'PASS' if result.ok else 'FAIL'} in {result.duration_seconds:.1f}s",
            flush=True,
        )
        if not result.ok:
            print(result.output_tail, flush=True)
            break

    evidence_dir = ROOT / "RobinWorkspace" / "sessions" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc)
    evidence_path = evidence_dir / f"operator-eval-{timestamp.strftime('%Y%m%dT%H%M%SZ')}.json"
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    passed = len(results) == len(commands) and all(result.ok for result in results)
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_at": timestamp.isoformat(),
                "commit": commit,
                "live_models": args.live_models or args.live_only,
                "live_only": args.live_only,
                "passed": passed,
                "results": [asdict(result) for result in results],
                "limitations": [
                    "This evaluation does not prove another Meet participant heard or saw Robin.",
                    "Three fresh-start real Meet rehearsals remain a separate completion gate.",
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Evidence: {evidence_path}")
    if not passed:
        raise SystemExit("Operator evaluation failed.")
    print(f"Operator evaluation passed ({len(results)} checks).")


if __name__ == "__main__":
    main()
