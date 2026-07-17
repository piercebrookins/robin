from __future__ import annotations

import asyncio
import json
import re
from uuid import UUID

from openai import AsyncOpenAI

from .config import Settings
from .schemas import MeetingIntent, RobinTask


class IntentClassifier:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    async def classify(self, text: str, active_tasks: list[RobinTask]) -> MeetingIntent:
        if self.client:
            try:
                return await asyncio.wait_for(
                    self._classify_openai(text, active_tasks),
                    timeout=self.settings.model.intent_timeout_seconds,
                )
            except Exception:
                return self._classify_local(text, active_tasks)
        return self._classify_local(text, active_tasks)

    async def _classify_openai(self, text: str, active_tasks: list[RobinTask]) -> MeetingIntent:
        active = [{"id": str(task.id), "title": task.title, "status": task.status} for task in active_tasks]
        response = await self.client.responses.create(
            model=self.settings.model.primary,
            input=[
                {"role": "system", "content": "Classify meeting turns for Robin. Return only JSON."},
                {"role": "user", "content": json.dumps({"turn": text, "active_tasks": active})},
            ],
            text={"format": {"type": "json_object"}},
        )
        raw = response.output_text
        return MeetingIntent.model_validate_json(raw)

    def _classify_local(self, text: str, active_tasks: list[RobinTask]) -> MeetingIntent:
        lowered = text.lower()
        addressed = bool(re.search(r"\brobin\b", lowered))
        is_cancel = any(word in lowered for word in ["cancel", "stop working", "never mind"])
        is_status = any(word in lowered for word in ["status", "how is", "where are we"])
        is_mod = bool(active_tasks) and any(word in lowered for word in ["add", "change", "use", "instead", "exclude", "include", "make it"])
        asks_work = any(phrase in lowered for phrase in ["make", "create", "build", "compare", "analyze", "show", "find", "pull"])
        ref_id: UUID | None = active_tasks[0].id if active_tasks and (is_mod or is_cancel or is_status) else None
        if is_cancel:
            classification = "task_cancellation"
        elif is_status:
            classification = "status_request"
        elif is_mod:
            classification = "task_modification"
        elif addressed and asks_work:
            classification = "direct_request"
        elif asks_work:
            classification = "possible_task"
        else:
            classification = "non_task"
        accepted = classification in {"direct_request", "task_modification", "task_cancellation", "status_request"}
        confidence = 0.92 if accepted else 0.55 if classification == "possible_task" else 0.2
        return MeetingIntent(
            classification=classification,
            confidence=confidence,
            addressed_to_robin=addressed,
            task_title=self._title_from_text(text) if accepted else None,
            requested_outcome=text if accepted else None,
            constraints=self._constraints(text),
            referenced_task_id=ref_id,
            should_acknowledge=accepted,
            should_ask_confirmation=classification == "possible_task",
            clarification_question="Should I take that on?" if classification == "possible_task" else None,
        )

    def _title_from_text(self, text: str) -> str:
        cleaned = re.sub(r"(?i)\brobin\b[:,]?\s*", "", text).strip()
        return cleaned[:80] or "Meeting task"

    def _constraints(self, text: str) -> list[str]:
        lowered = text.lower()
        constraints: list[str] = []
        if "actual" in lowered:
            constraints.append("Use actuals instead of forecasts")
        if "operating margin" in lowered:
            constraints.append("Include operating margin")
        if "forecast" in lowered and "exclude" in lowered:
            constraints.append("Exclude forecasted values")
        return constraints
