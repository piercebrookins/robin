from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from robin_core.supervisor import ManagedProcess, Supervisor, default_processes, wait_for_http


@pytest.mark.asyncio
async def test_wait_for_http_accepts_healthy_local_server() -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(1024)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        await wait_for_http(f"http://127.0.0.1:{port}", timeout_s=2, interval_s=0.05)
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_supervisor_starts_and_stops_managed_process(tmp_path: Path) -> None:
    managed = ManagedProcess(
        name="sleepy",
        command=[sys.executable, "-c", "import time; print('ready'); time.sleep(30)"],
    )
    supervisor = Supervisor([managed], log_dir=tmp_path, restart_backoff_s=0.01)

    await supervisor.start_process(managed)
    assert managed.process is not None
    assert managed.process.returncode is None

    await supervisor.stop()

    assert managed.process.returncode is not None
    assert (tmp_path / "sleepy.log").exists()


def test_default_processes_describe_core_and_web() -> None:
    processes = default_processes(Path("/tmp/robin"))

    assert [process.name for process in processes] == ["robin-core", "robin-web"]
    assert processes[0].health_url == "http://127.0.0.1:8787/health"
    assert processes[1].health_url == "http://127.0.0.1:3000"


def test_default_processes_use_production_web_runtime_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBIN_WEB_MODE", "production")

    processes = default_processes(Path("/tmp/robin"))

    assert processes[1].command == [
        "node",
        str(
            Path("/tmp/robin").resolve()
            / "apps/web/node_modules/next/dist/bin/next"
        ),
        "start",
        "-H",
        "127.0.0.1",
        "-p",
        "3000",
    ]
