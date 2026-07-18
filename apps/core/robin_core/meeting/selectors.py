from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SelectorCandidate:
    role: str | None = None
    name_regex: str | None = None
    test_id: str | None = None
    text_regex: str | None = None


MEET_SELECTORS: dict[str, list[SelectorCandidate]] = {
    "join_button": [
        SelectorCandidate(role="button", name_regex=r"Join now|Ask to join"),
        SelectorCandidate(test_id="join-button"),
    ],
    "leave_button": [
        SelectorCandidate(role="button", name_regex=r"Leave call|Leave meeting"),
        SelectorCandidate(test_id="leave-button"),
    ],
    "mute_button": [
        SelectorCandidate(
            role="button", name_regex=r"Turn off microphone|Mute microphone|Microphone"
        ),
        SelectorCandidate(test_id="mute-button"),
    ],
    "unmute_button": [
        SelectorCandidate(
            role="button", name_regex=r"Turn on microphone|Unmute microphone|Microphone"
        ),
        SelectorCandidate(test_id="unmute-button"),
    ],
    "camera_button": [
        SelectorCandidate(role="button", name_regex=r"Turn off camera|Turn on camera|Camera"),
        SelectorCandidate(test_id="camera-button"),
    ],
    "present_button": [
        SelectorCandidate(role="button", name_regex=r"Present now|Share screen|Present"),
        SelectorCandidate(test_id="present-button"),
    ],
    "share_tab_option": [
        SelectorCandidate(role="menuitem", name_regex=r"A tab|Chrome tab|Share a tab"),
        SelectorCandidate(role="button", name_regex=r"A tab|Chrome tab|Share a tab"),
        SelectorCandidate(test_id="share-tab-option"),
    ],
    "stop_presenting_button": [
        SelectorCandidate(role="button", name_regex=r"Stop presenting|Stop sharing"),
        SelectorCandidate(test_id="stop-presenting-button"),
    ],
    "presenting_signal": [
        SelectorCandidate(text_regex=r"You are presenting"),
        SelectorCandidate(role="button", name_regex=r"Stop presenting|Stop sharing"),
        SelectorCandidate(test_id="presenting-signal"),
    ],
    "joined_signal": [
        SelectorCandidate(role="button", name_regex=r"Leave call|Leave meeting"),
        SelectorCandidate(test_id="joined-signal"),
    ],
}
