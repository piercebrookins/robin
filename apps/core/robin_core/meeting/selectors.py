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
        SelectorCandidate(
            role="button",
            name_regex=r"^(?:Turn off microphone|Mute microphone)(?:\b.*)?$",
        ),
        SelectorCandidate(test_id="mute-button"),
    ],
    "unmute_button": [
        SelectorCandidate(
            role="button",
            name_regex=r"^(?:Turn on microphone|Unmute microphone)(?:\b.*)?$",
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
    "reactions_button": [
        SelectorCandidate(role="button", name_regex=r"Reactions|Activities"),
        SelectorCandidate(test_id="reactions-button"),
    ],
    "raise_hand_button": [
        SelectorCandidate(role="button", name_regex=r"Raise hand"),
        SelectorCandidate(role="menuitem", name_regex=r"Raise hand"),
        SelectorCandidate(test_id="raise-hand-button"),
    ],
    "lower_hand_button": [
        SelectorCandidate(role="button", name_regex=r"Lower hand"),
        SelectorCandidate(role="menuitem", name_regex=r"Lower hand"),
        SelectorCandidate(test_id="lower-hand-button"),
    ],
    "hand_raised_signal": [
        SelectorCandidate(text_regex=r"Your hand is raised|You raised your hand"),
        SelectorCandidate(role="button", name_regex=r"Lower hand"),
        SelectorCandidate(test_id="hand-raised-signal"),
    ],
    "share_tab_option": [
        SelectorCandidate(role="menuitem", name_regex=r"A tab|Chrome tab|Share a tab"),
        SelectorCandidate(role="button", name_regex=r"A tab|Chrome tab|Share a tab"),
        SelectorCandidate(test_id="share-tab-option"),
    ],
    "stop_presenting_button": [
        SelectorCandidate(role="button", name_regex=r"^(?:Stop presenting|Stop sharing)$"),
        SelectorCandidate(test_id="stop-presenting-button"),
    ],
    "presenting_signal": [
        SelectorCandidate(text_regex=r"You are presenting"),
        SelectorCandidate(role="button", name_regex=r"^(?:Stop presenting|Stop sharing)$"),
        SelectorCandidate(test_id="presenting-signal"),
    ],
    "joined_signal": [
        SelectorCandidate(role="button", name_regex=r"Leave call|Leave meeting"),
        SelectorCandidate(test_id="joined-signal"),
    ],
    "in_call_signal": [
        SelectorCandidate(role="button", name_regex=r"Present now|Share screen|Present"),
        SelectorCandidate(
            role="button",
            name_regex=r"Turn on captions|Show captions|Enable captions|Turn off captions|Hide captions|Disable captions",
        ),
        SelectorCandidate(test_id="in-call-signal"),
    ],
    "enable_captions_button": [
        SelectorCandidate(
            role="button", name_regex=r"Turn on captions|Show captions|Enable captions"
        ),
        SelectorCandidate(test_id="enable-captions-button"),
    ],
    "disable_captions_button": [
        SelectorCandidate(
            role="button", name_regex=r"Turn off captions|Hide captions|Disable captions"
        ),
        SelectorCandidate(test_id="disable-captions-button"),
    ],
}
