from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from robin_core.agent import GeneralTaskAgent
from robin_core.artifacts import ArtifactWorker
from robin_core.config import Settings, WorkspaceConfig, load_settings
from robin_core.schemas import RobinTask
from robin_core.workspace import Workspace


REQUESTS = [
    "Identify the most important business context and caveats and prepare a concise briefing.",
    "Compare the available evidence and explain what an executive should verify next.",
    "Create a concise meeting-notes.md file with sourced decisions, caveats, and next steps, plus a short briefing.",
]


async def main() -> None:
    live = load_settings()
    if not live.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is required for the real general-agent smoke test.")
    with tempfile.TemporaryDirectory(prefix="robin-agent-smoke-") as temporary:
        root = Path(temporary)
        shutil.copytree(live.workspace.root / live.workspace.source_dir, root / "source-data")
        settings = Settings(
            openai_api_key=live.openai_api_key,
            model=live.model,
            workspace=WorkspaceConfig(
                root=root,
                allowed_extensions=live.workspace.allowed_extensions,
                max_file_size_mb=live.workspace.max_file_size_mb,
            ),
        )
        workspace = Workspace(settings.workspace)
        records = workspace.index()
        agent = GeneralTaskAgent(settings, workspace)
        worker = ArtifactWorker(workspace, live.presentation.base_url)
        for index, request in enumerate(REQUESTS, start=1):
            task = RobinTask(
                meeting_id=uuid4(),
                title=f"General agent smoke {index}",
                request_text=request,
                requested_outcome=request,
            )
            result = await agent.execute(task, records)
            artifacts, _deck, validation = worker.write_agent_result(task, result)
            if not validation.ok:
                raise SystemExit(f"Task {index} failed validation.")
            if not any(call.get("tool") == "read_workspace_file" for call in result.tool_calls):
                raise SystemExit(f"Task {index} did not inspect a workspace source.")
            if index == len(REQUESTS) and not result.generated_paths:
                raise SystemExit("Generated-file task did not create the requested file.")
            print(
                f"{index}/{len(REQUESTS)} passed: {result.deliverable.title!r}; "
                f"{len(result.source_paths)} source(s), {len(artifacts)} artifact(s), "
                f"{result.iterations} iteration(s)"
            )
    print("General-agent smoke passed with three different real model tasks, including file creation.")


if __name__ == "__main__":
    asyncio.run(main())
