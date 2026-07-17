from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


@dataclass
class ManagedProcess:
    name: str
    command: list[str]
    cwd: Path | None = None
    env: dict[str, str] | None = None
    health_url: str | None = None
    restart: bool = True
    process: asyncio.subprocess.Process | None = None


class Supervisor:
    def __init__(
        self,
        processes: list[ManagedProcess],
        log_dir: Path | str = Path("RobinWorkspace/sessions/logs"),
        health_timeout_s: float = 30,
        restart_backoff_s: float = 2,
    ):
        self.processes = processes
        self.log_dir = Path(log_dir)
        self.health_timeout_s = health_timeout_s
        self.restart_backoff_s = restart_backoff_s
        self._stopping = asyncio.Event()
        self._monitors: list[asyncio.Task] = []
        self._log_handles = []

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stopping.set)
            except NotImplementedError:
                pass
        for process in self.processes:
            await self.start_process(process)
            self._monitors.append(asyncio.create_task(self._monitor(process)))
        await self.wait_for_health()
        await self._stopping.wait()
        await self.stop()

    async def start_process(self, process: ManagedProcess) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"{process.name}.log"
        log = log_path.open("ab")
        self._log_handles.append(log)
        env = os.environ.copy()
        env.update(process.env or {})
        process.process = await asyncio.create_subprocess_exec(
            *process.command,
            cwd=process.cwd,
            env=env,
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
        )
        print(f"started {process.name} pid={process.process.pid} log={log_path}")

    async def wait_for_health(self) -> None:
        for process in self.processes:
            if not process.health_url:
                continue
            await wait_for_http(process.health_url, timeout_s=self.health_timeout_s)
            print(f"healthy {process.name}: {process.health_url}")

    async def stop(self) -> None:
        self._stopping.set()
        for monitor in self._monitors:
            monitor.cancel()
        for process in self.processes:
            if process.process and process.process.returncode is None:
                process.process.terminate()
        await asyncio.gather(
            *(process.process.wait() for process in self.processes if process.process),
            return_exceptions=True,
        )
        for handle in self._log_handles:
            handle.close()

    async def _monitor(self, managed: ManagedProcess) -> None:
        while not self._stopping.is_set():
            if managed.process is None:
                return
            returncode = await managed.process.wait()
            if self._stopping.is_set() or not managed.restart:
                return
            print(f"{managed.name} exited with {returncode}; restarting in {self.restart_backoff_s}s")
            await asyncio.sleep(self.restart_backoff_s)
            await self.start_process(managed)


async def wait_for_http(url: str, timeout_s: float = 30, interval_s: float = 0.5) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    last_error: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            ok = await asyncio.to_thread(_http_ok, url)
            if ok:
                return
        except Exception as exc:
            last_error = exc
        await asyncio.sleep(interval_s)
    raise TimeoutError(f"Timed out waiting for {url}: {last_error}")


def _http_ok(url: str) -> bool:
    try:
        with urlopen(url, timeout=2) as response:
            return 200 <= response.status < 500
    except URLError:
        return False


def default_processes(root: Path | None = None) -> list[ManagedProcess]:
    repo = (root or Path.cwd()).resolve()
    return [
        ManagedProcess(
            name="robin-core",
            command=[
                "uv",
                "run",
                "uvicorn",
                "robin_core.main:app",
                "--app-dir",
                "apps/core",
                "--host",
                "127.0.0.1",
                "--port",
                "8787",
            ],
            cwd=repo,
            health_url="http://127.0.0.1:8787/health",
        ),
        ManagedProcess(
            name="robin-web",
            command=["pnpm", "--dir", "apps/web", "dev"],
            cwd=repo,
            health_url="http://127.0.0.1:3000",
        ),
    ]
