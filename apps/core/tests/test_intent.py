from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from robin_core.config import ModelConfig, PresentationConfig, Settings
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
async def test_addressed_ambiguous_request_still_requires_confirmation() -> None:
    classifier = IntentClassifier(Settings())

    intent = await classifier.classify(
        "Robin, could someone compare the finance files and make slides?", []
    )

    assert intent.classification == "possible_task"
    assert intent.addressed_to_robin is True
    assert intent.should_ask_confirmation is True


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


@pytest.mark.asyncio
async def test_presentation_invitation_requires_pending_handoff_and_wake_word() -> None:
    classifier = IntentClassifier(Settings())
    task_id = uuid4()
    pending = {
        "task_id": str(task_id),
        "title": "Launch review",
        "revision": 1,
        "state": "WAITING_FOR_INVITATION",
        "hand_raised": True,
    }

    accepted = await classifier.classify("Robin, you can share now.", [], pending)
    missing_wake = await classifier.classify("You can share now.", [], pending)
    no_pending = await classifier.classify("Robin, you can share now.", [])

    assert accepted.classification == "presentation_invitation"
    assert accepted.referenced_task_id == task_id
    assert missing_wake.classification != "presentation_invitation"
    assert no_pending.classification != "presentation_invitation"


@pytest.mark.asyncio
async def test_presentation_invitation_rejects_hand_state_mentions() -> None:
    classifier = IntentClassifier(Settings())
    pending = {
        "task_id": str(uuid4()),
        "title": "Launch review",
        "revision": 1,
        "state": "WAITING_FOR_INVITATION",
        "hand_raised": True,
    }

    intent = await classifier.classify("Did Robin raise its hand?", [], pending)

    assert intent.classification != "presentation_invitation"


@pytest.mark.asyncio
async def test_presentation_invitation_can_relax_wake_word_requirement() -> None:
    classifier = IntentClassifier(
        Settings(presentation=PresentationConfig(require_wake_word_for_invitation=False))
    )
    task_id = uuid4()
    pending = {
        "task_id": str(task_id),
        "title": "Launch review",
        "revision": 1,
        "state": "WAITING_FOR_INVITATION",
        "hand_raised": True,
        "require_wake_word": False,
    }

    intent = await classifier.classify("You can share now.", [], pending)

    assert intent.classification == "presentation_invitation"
    assert intent.referenced_task_id == task_id
