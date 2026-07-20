from __future__ import annotations

import socket
import shutil
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .browser.native_dialog import computer_use_permissions_granted
from .config import Settings
from .schemas import HealthItem


def run_preflight(settings: Settings) -> list[HealthItem]:
    source_workspace = settings.workspace.root / settings.workspace.source_dir
    checks = [
        HealthItem(
            name="workspace_root",
            ok=settings.workspace.root.exists(),
            detail=str(settings.workspace.root),
        ),
        HealthItem(
            name="source_workspace",
            ok=source_workspace.exists(),
            detail=str(source_workspace),
        ),
        HealthItem(
            name="openai_api_key",
            ok=bool(settings.openai_api_key),
            detail="configured" if settings.openai_api_key else "missing",
        ),
        _workspace_files_check(settings),
        _database_write_check(settings),
        _disk_space_check(settings),
        _internet_check(),
        _google_session_check(settings),
        _screen_recording_check(settings),
        _accessibility_check(settings),
        _presentation_renderer_check(settings),
        _dashboard_check(),
        _browser_debugging_check(settings),
        _computer_use_check(settings),
    ]
    checks.append(_browser_check(settings))
    checks.append(_bridge_check(settings))
    checks.append(_blackhole_check(settings))
    return checks


def _browser_check(settings: Settings) -> HealthItem:
    if settings.browser.automation_mode == "simulator":
        return HealthItem(name="chrome", ok=True, detail="simulator mode does not require Chrome")
    candidates: list[Path | None] = [
        settings.browser.executable_path,
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        _which_path("google-chrome"),
        _which_path("chromium"),
    ]
    found = next((candidate for candidate in candidates if candidate and candidate.exists()), None)
    return HealthItem(
        name="chrome",
        ok=found is not None,
        detail=str(found) if found else "Chrome/Chromium executable not found",
    )


def _computer_use_check(settings: Settings) -> HealthItem:
    if settings.browser.share_dialog_mode == "simulator":
        return HealthItem(name="computer_use", ok=True, detail="simulator share dialog")
    if settings.browser.share_dialog_mode != "cua_driver":
        return HealthItem(
            name="computer_use",
            ok=False,
            detail=f"unsupported share dialog mode: {settings.browser.share_dialog_mode}",
        )
    executable = shutil.which(settings.browser.computer_use_command)
    if not executable:
        return HealthItem(
            name="computer_use",
            ok=False,
            detail=f"{settings.browser.computer_use_command} is not installed or not on PATH",
        )
    try:
        completed = subprocess.run(
            [executable, "check_permissions", '{"prompt":false}'],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        return HealthItem(name="computer_use", ok=False, detail=f"permission check failed: {exc}")
    output = f"{completed.stdout}\n{completed.stderr}"
    granted = completed.returncode == 0 and computer_use_permissions_granted(output)
    return HealthItem(
        name="computer_use",
        ok=granted,
        detail="Codex/macOS Computer Use permissions granted"
        if granted
        else "Accessibility or Screen Recording permission missing",
    )


def _which_path(command: str) -> Path | None:
    found = shutil.which(command)
    return Path(found) if found else None


def _blackhole_check(settings: Settings) -> HealthItem:
    if settings.audio.mode == "simulator":
        return HealthItem(
            name="blackhole", ok=True, detail="simulator mode does not require a virtual microphone"
        )
    blackhole_paths = [
        Path("/Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver"),
        Path("/Library/Audio/Plug-Ins/HAL/BlackHole16ch.driver"),
    ]
    found = next((path for path in blackhole_paths if path.exists()), None)
    return HealthItem(
        name="blackhole",
        ok=found is not None,
        detail=str(found) if found else "BlackHole audio driver not found",
    )


def _bridge_check(settings: Settings) -> HealthItem:
    if settings.audio.bridge_mode == "simulator":
        return HealthItem(name="macos_bridge", ok=True, detail="simulator bridge")
    executable = settings.audio.bridge_executable
    ok = bool(executable and executable.exists())
    return HealthItem(
        name="macos_bridge",
        ok=ok,
        detail=str(executable) if ok else "bridge executable not configured or not found",
    )


def _workspace_files_check(settings: Settings) -> HealthItem:
    source = settings.workspace.root / settings.workspace.source_dir
    if not source.exists():
        return HealthItem(
            name="workspace_files", ok=False, detail=f"source directory missing: {source}"
        )
    max_bytes = settings.workspace.max_file_size_mb * 1024 * 1024
    files = [
        path
        for path in source.rglob("*")
        if path.is_file()
        and path.suffix.lower() in settings.workspace.allowed_extensions
        and path.stat().st_size <= max_bytes
    ]
    return HealthItem(
        name="workspace_files",
        ok=bool(files),
        detail=f"{len(files)} usable file(s)" if files else f"no supported files in {source}",
    )


def _database_write_check(settings: Settings) -> HealthItem:
    try:
        settings.database.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(settings.database.path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS preflight_write_check (id INTEGER PRIMARY KEY, checked_at TEXT DEFAULT CURRENT_TIMESTAMP)"
            )
            cursor = conn.execute("INSERT INTO preflight_write_check DEFAULT VALUES")
            conn.execute("DELETE FROM preflight_write_check WHERE id = ?", (cursor.lastrowid,))
            conn.commit()
    except Exception as exc:
        return HealthItem(name="database_write", ok=False, detail=str(exc))
    return HealthItem(name="database_write", ok=True, detail=str(settings.database.path))


def _disk_space_check(settings: Settings) -> HealthItem:
    try:
        usage = shutil.disk_usage(settings.workspace.root)
    except FileNotFoundError:
        return HealthItem(
            name="disk_space", ok=False, detail=f"workspace root missing: {settings.workspace.root}"
        )
    free_mb = usage.free // (1024 * 1024)
    required_mb = settings.runtime.min_free_disk_mb
    return HealthItem(
        name="disk_space",
        ok=free_mb >= required_mb,
        detail=f"{free_mb} MB free; requires {required_mb} MB",
    )


def _internet_check() -> HealthItem:
    try:
        with socket.create_connection(("api.openai.com", 443), timeout=1.5):
            pass
    except OSError as exc:
        return HealthItem(
            name="internet", ok=False, detail=f"cannot reach api.openai.com:443 ({exc})"
        )
    return HealthItem(name="internet", ok=True, detail="api.openai.com:443 reachable")


def _google_session_check(settings: Settings) -> HealthItem:
    if settings.browser.automation_mode == "simulator":
        return HealthItem(
            name="google_account_session",
            ok=True,
            detail="simulator mode does not require Google login",
        )
    profile = settings.browser.profile_dir.expanduser()
    has_profile_state = any(
        (profile / candidate).exists() for candidate in ("Default", "Profile 1", "Local State")
    )
    return HealthItem(
        name="google_account_session",
        ok=has_profile_state,
        detail=str(profile)
        if has_profile_state
        else f"Chrome profile has no saved Google session state: {profile}",
    )


def _screen_recording_check(settings: Settings) -> HealthItem:
    if settings.audio.bridge_mode == "simulator":
        return HealthItem(
            name="screen_recording_permission",
            ok=True,
            detail="simulator bridge does not capture the screen",
        )
    return HealthItem(
        name="screen_recording_permission",
        ok=True,
        detail="requires macOS permission for the configured bridge process; refresh bridge health to verify live access",
    )


def _accessibility_check(settings: Settings) -> HealthItem:
    if settings.browser.automation_mode == "simulator":
        return HealthItem(
            name="accessibility_permission",
            ok=True,
            detail="simulator mode does not control Chrome UI",
        )
    return HealthItem(
        name="accessibility_permission",
        ok=True,
        detail="requires macOS Accessibility permission for the controller process when driving real Chrome",
    )


def _presentation_renderer_check(settings: Settings) -> HealthItem:
    parsed = urlparse(settings.presentation.base_url)
    ok = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    return HealthItem(
        name="presentation_renderer",
        ok=ok,
        detail=settings.presentation.base_url
        if ok
        else f"invalid presentation URL: {settings.presentation.base_url}",
    )


def _dashboard_check() -> HealthItem:
    try:
        request = Request("http://127.0.0.1:3000", method="GET")
        with urlopen(request, timeout=1.0) as response:
            ok = 200 <= response.status < 500
    except Exception as exc:
        return HealthItem(
            name="dashboard", ok=False, detail=f"http://127.0.0.1:3000 not reachable ({exc})"
        )
    return HealthItem(name="dashboard", ok=ok, detail="http://127.0.0.1:3000 reachable")


def _browser_debugging_check(settings: Settings) -> HealthItem:
    if settings.browser.automation_mode == "simulator":
        return HealthItem(
            name="browser_debugging",
            ok=True,
            detail="simulator mode does not require Chrome debugging",
        )
    try:
        with socket.create_connection(
            ("127.0.0.1", settings.browser.remote_debugging_port), timeout=0.5
        ):
            pass
    except OSError as exc:
        return HealthItem(
            name="browser_debugging",
            ok=False,
            detail=f"Chrome remote debugging port {settings.browser.remote_debugging_port} not reachable ({exc})",
        )
    return HealthItem(
        name="browser_debugging",
        ok=True,
        detail=f"127.0.0.1:{settings.browser.remote_debugging_port} reachable",
    )
