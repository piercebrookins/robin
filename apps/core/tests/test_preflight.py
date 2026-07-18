from __future__ import annotations

from pathlib import Path

from robin_core import preflight
from robin_core.config import DatabaseConfig, RuntimeConfig, Settings, WorkspaceConfig
from robin_core.schemas import HealthItem


def _settings(tmp_path: Path) -> Settings:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (source / "quarterly.csv").write_text("quarter,revenue\nQ1,100\n")
    return Settings(
        runtime=RuntimeConfig(min_free_disk_mb=1),
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        openai_api_key="test-key",
    )


def test_preflight_verifies_workspace_database_disk_and_services(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(preflight, "_internet_check", lambda: HealthItem(name="internet", ok=True, detail="stubbed"))
    monkeypatch.setattr(preflight, "_dashboard_check", lambda: HealthItem(name="dashboard", ok=True, detail="stubbed"))

    checks = {item.name: item for item in preflight.run_preflight(_settings(tmp_path))}

    assert checks["workspace_files"].ok
    assert checks["database_write"].ok
    assert checks["disk_space"].ok
    assert checks["internet"].ok
    assert checks["dashboard"].ok


def test_preflight_fails_when_no_supported_workspace_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(preflight, "_internet_check", lambda: HealthItem(name="internet", ok=True, detail="stubbed"))
    monkeypatch.setattr(preflight, "_dashboard_check", lambda: HealthItem(name="dashboard", ok=True, detail="stubbed"))
    settings = _settings(tmp_path)
    for path in (settings.workspace.root / settings.workspace.source_dir).iterdir():
        path.unlink()

    checks = {item.name: item for item in preflight.run_preflight(settings)}

    assert not checks["workspace_files"].ok
