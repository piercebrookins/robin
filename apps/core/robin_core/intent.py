from __future__ import annotations

import asyncio
import json
import re
from uuid import UUID

from openai import AsyncOpenAI

from .config import Settings
from .schemas import MeetingIntent, RobinTask, TranscriptSegment


class IntentClassifier:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    async def classify(self, text: str, active_tasks: list[RobinTask]) -> MeetingIntent:
        if self.client:
            try:
                intent = await asyncio.wait_for(
                    self._classify_openai(text, active_tasks),
                    timeout=self.settings.model.intent_timeout_seconds,
                )
                local = self._classify_local(text, active_tasks)
                if (
                    intent.classification == "non_task"
                    and local.classification == "conversation_request"
                ):
                    return local
                return intent
            except Exception:
                return self._classify_local(text, active_tasks)
        return self._classify_local(text, active_tasks)

    async def _classify_openai(self, text: str, active_tasks: list[RobinTask]) -> MeetingIntent:
        active = [{"id": str(task.id), "title": task.title, "status": task.status} for task in active_tasks]
        response = await self.client.responses.create(
            model=self.settings.model.primary,
            input=[
                {
                    "role": "system",
                    "content": (
                        "Classify meeting turns for Robin. Return only JSON matching MeetingIntent. "
                        "Use conversation_request when Robin is directly addressed with a greeting, "
                        "voice check, or brief question that is not a task/status/cancel request."
                    ),
                },
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
        elif addressed:
            classification = "conversation_request"
        elif asks_work:
            classification = "possible_task"
        else:
            classification = "non_task"
        accepted = classification in {
            "direct_request",
            "task_modification",
            "task_cancellation",
            "status_request",
            "conversation_request",
        }
        confidence = 0.92 if accepted else 0.55 if classification == "possible_task" else 0.2
        return MeetingIntent(
            classification=classification,
            confidence=confidence,
            addressed_to_robin=addressed,
            task_title=self._title_from_text(text) if classification == "direct_request" else None,
            requested_outcome=text if classification == "direct_request" else None,
            constraints=self._constraints(text),
            referenced_task_id=ref_id,
            should_acknowledge=accepted,
            should_ask_confirmation=classification == "possible_task",
            clarification_question="Should I take that on?" if classification == "possible_task" else None,
        )

    async def respond(
        self,
        text: str,
        active_tasks: list[RobinTask],
        meeting_context: list[TranscriptSegment] | None = None,
    ) -> str:
        lowered = text.casefold()
        if any(
            phrase in lowered
            for phrase in ("can you hear me", "do you hear me", "are you listening")
        ):
            return "Yes, I can hear you. I’m listening for requests addressed to Robin."
        if self.client:
            try:
                response = await asyncio.wait_for(
                    self.client.responses.create(
                        model=self.settings.model.primary,
                        input=[
                            {
                                "role": "system",
                                "content": (
                                    "You are Robin, a concise meeting coworker. Answer the directly "
                                    "addressed question in at most two short sentences. Do not claim to "
                                    "have performed work or accessed data unless the turn says so."
                                ),
                            },
                            {
                                "role": "user",
                                "content": json.dumps(
                                    {
                                        "turn": text,
                                        "active_tasks": [
                                            {"title": task.title, "status": task.status}
                                            for task in active_tasks
                                        ],
                                        "recent_meeting_context": [
                                            {
                                                "speaker": segment.speaker_name,
                                                "text": segment.text,
                                            }
                                            for segment in (meeting_context or [])[-20:]
                                        ],
                                    }
                                ),
                            },
                        ],
                    ),
                    timeout=self.settings.model.intent_timeout_seconds,
                )
                reply = response.output_text.strip()
                if reply:
                    return reply
            except Exception:
                pass
        return "I heard you. Ask me to analyze the workspace, prepare slides, or report task status."

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
