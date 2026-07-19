from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from robin_core.browser.controller import BrowserController
from robin_core.browser.operator_agent import ControlledBrowserAgent
from robin_core.browser.page_driver import InteractiveElement, SimulatedPageDriver
from robin_core.config import ModelConfig, Settings


class FakeResponses:
    def __init__(self, outputs: list[list[SimpleNamespace]]):
        self.outputs = outputs

    async def create(self, **_kwargs):
        return SimpleNamespace(output=self.outputs.pop(0))


class FakeClient:
    def __init__(self, outputs: list[list[SimpleNamespace]]):
        self.responses = FakeResponses(outputs)


def call(name: str, call_id: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call",
        name=name,
        call_id=call_id,
        arguments=json.dumps(arguments),
    )


def make_agent(element: InteractiveElement) -> tuple[ControlledBrowserAgent, SimulatedPageDriver]:
    settings = Settings(openai_api_key="test-key", model=ModelConfig(agent_max_iterations=8))
    controller = BrowserController()
    page = SimulatedPageDriver(operator_elements={element.ref: element})
    controller.pages["meet"] = page
    return ControlledBrowserAgent(settings, controller), page


@pytest.mark.asyncio
async def test_model_browser_loop_inspects_acts_reinspects_and_finishes() -> None:
    agent, page = make_agent(
        InteractiveElement("e1", "button", "More options", "button")
    )
    agent.client = FakeClient(
        [
            [call("inspect_page", "inspect-1", {})],
            [call("click_element", "click-1", {"ref": "e1"})],
            [call("finish_browser_task", "early", {"summary": "Done"})],
            [call("inspect_page", "inspect-2", {})],
            [call("finish_browser_task", "finish", {"summary": "Menu opened"})],
        ]
    )  # type: ignore[assignment]

    result = await agent.execute("Open more options", "meet")

    assert result.status == "completed"
    assert result.summary == "Menu opened"
    assert page.clicked == ["e1"]
    assert any(item.get("verified") is False for item in result.tool_calls)


@pytest.mark.asyncio
async def test_risky_model_action_pauses_and_exact_approval_resumes() -> None:
    agent, page = make_agent(
        InteractiveElement("e1", "button", "Join now", "button")
    )
    agent.client = FakeClient(
        [
            [call("inspect_page", "inspect", {})],
            [call("click_element", "click", {"ref": "e1"})],
        ]
    )  # type: ignore[assignment]

    pending = await agent.execute("Join the meeting", "meet")

    assert pending.status == "awaiting_confirmation"
    assert pending.approval_token
    assert page.clicked == []

    agent.client = FakeClient(
        [
            [call("inspect_page", "inspect-2", {})],
            [call("click_element", "click-2", {"ref": "e1"})],
            [call("inspect_page", "inspect-3", {})],
            [call("finish_browser_task", "finish", {"summary": "Join clicked"})],
        ]
    )  # type: ignore[assignment]
    completed = await agent.execute(
        "Join the meeting", "meet", approval_token=pending.approval_token
    )

    assert completed.status == "completed"
    assert page.clicked == ["e1"]


@pytest.mark.asyncio
async def test_approval_token_is_bound_to_exact_filled_text() -> None:
    agent, _ = make_agent(
        InteractiveElement("e1", "textbox", "Send message", "textarea")
    )
    original = {"ref": "e1", "text": "Approved message"}
    altered = {"ref": "e1", "text": "Different message"}
    token = agent._approval_token("meet", "fill", "e1", "Send message", original)

    assert token != agent._approval_token(
        "meet", "fill", "e1", "Send message", altered
    )
