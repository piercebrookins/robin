from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SelectorCandidate:
    role: str | None = None
    name_regex: str | None = None
    test_id: str | None = None
    text_regex: str | None = None


MEET_SELECTORS: dict[str, list[SelectorCandidate]] = {
    "prejoin_mute_button": [
        SelectorCandidate(role="button", name_regex=r"Turn off microphone|Mute microphone"),
    ],
    "prejoin_camera_button": [
        SelectorCandidate(role="button", name_regex=r"Turn off camera"),
    ],
    "join_button": [
        SelectorCandidate(role="button", name_regex=r"Join now|Ask to join"),
        SelectorCandidate(test_id="join-button"),
    ],
    "leave_button": [
        SelectorCandidate(role="button", name_regex=r"Leave call|Leave meeting"),
        SelectorCandidate(test_id="leave-button"),
    ],
    "mute_button": [
        SelectorCandidate(role="button", name_regex=r"Turn off microphone|Mute microphone|Microphone"),
        SelectorCandidate(test_id="mute-button"),
    ],
    "unmute_button": [
        SelectorCandidate(role="button", name_regex=r"Turn on microphone|Unmute microphone|Microphone"),
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
    "stop_presenting_button": [
        SelectorCandidate(role="button", name_regex=r"Stop presenting|Stop sharing"),
        SelectorCandidate(test_id="stop-presenting-button"),
    ],
    "joined_signal": [
        SelectorCandidate(role="button", name_regex=r"Leave call|Leave meeting"),
        SelectorCandidate(test_id="joined-signal"),
    ],
}
