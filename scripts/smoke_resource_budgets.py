from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.config import load_settings
from robin_core.runtime import RobinRuntime


def main() -> None:
    settings = load_settings()
    runtime = RobinRuntime(settings)
    metrics = runtime.metrics()
    violations = runtime._resource_budget_violations(
        metrics.peak_rss_mb,
        metrics.workspace_disk_mb,
    )
    if violations:
        raise SystemExit("; ".join(violations))
    if metrics.peak_rss_mb <= 0:
        raise SystemExit("Peak RSS measurement was unavailable")
    print(
        "Resource budgets passed: "
        f"peak_rss={metrics.peak_rss_mb:.1f}/{settings.runtime.max_peak_rss_mb} MB "
        f"workspace={metrics.workspace_disk_mb:.1f}/{settings.runtime.max_workspace_disk_mb} MB"
    )


if __name__ == "__main__":
    main()
