from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from robin_core.config import ModelConfig, Settings
from robin_core.intent import IntentClassifier
from robin_core.schemas import MeetingIntent, RobinTask, TaskStatus


@pytest.mark.asyncio
async def test_openai_intent_timeout_falls_back_to_local_classifier() -> None:
    settings = Settings(openai_api_key="test-key", model=ModelConfig(intent_timeout_seconds=0.01))
    classifier = IntentClassifier(settings)

    async def slow_openai(_text, _active_tasks):
        await asyncio.sleep(1)
        return MeetingIntent(classification="non_task", confidence=1, addressed_to_robin=False)

    classifier._classify_openai = slow_openai  # type: ignore[method-assign]

    intent = await classifier.classify("Could someone compare the finance files?", [])

    assert intent.classification == "possible_task"
    assert intent.should_ask_confirmation is True


@pytest.mark.asyncio
async def test_addressed_voice_check_is_a_conversation_request() -> None:
    classifier = IntentClassifier(Settings())

    intent = await classifier.classify("Robin, can you hear me?", [])
    reply = await classifier.respond("Robin, can you hear me?", [])

    assert intent.classification == "conversation_request"
    assert intent.addressed_to_robin is True
    assert reply.startswith("Yes, I can hear you.")


@pytest.mark.asyncio
async def test_source_question_is_not_misclassified_as_task_revision() -> None:
    classifier = IntentClassifier(Settings())
    task = RobinTask(
        meeting_id=uuid4(),
        title="Launch review",
        status=TaskStatus.READY_TO_PRESENT,
        request_text="Review launch files",
        requested_outcome="Readiness deck",
    )

    intent = await classifier.classify("Robin, what sources did you use?", [task])

    assert intent.classification == "conversation_request"
