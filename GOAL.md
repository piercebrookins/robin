# Goal: Build Robin

Build and ship Robin as a fully working, reproducible, Mac-hosted agentic coworker that joins ordinary Zoom meetings through the normal Zoom Workplace app—without the Zoom SDK—and participates as a dedicated signed-in Zoom user.

Robin must use GPT Realtime for natural, interruptible two-way speech and GPT-5.6 computer use to see and operate the entire Mac desktop. It must independently open and control Zoom, join a supplied meeting link, handle normal meeting states, listen and respond, accept spoken work requests, operate approved desktop applications to complete them, share and stop sharing its screen through Zoom, report results, and leave the meeting.

Implement:

- A TypeScript/Node.js daemon coordinating Zoom lifecycle, Realtime voice, GPT-5.6 task execution, policy, recovery, and audit events.
- A signed Swift helper providing ScreenCaptureKit screenshots, Accessibility-first UI actions, CGEvent fallback input, window metadata, and macOS permission checks.
- A Core Audio bridge using stable BlackHole-based virtual devices to send Zoom meeting audio to Realtime and Realtime speech to Zoom, with resampling, echo prevention, barge-in, bounded buffering, and reconnect handling.
- A private web control panel for meeting links, task state, transcripts, approvals, mute/share/leave controls, emergency stop, health diagnostics, and human takeover.
- Persistent `launchd` services, secure Keychain-based secrets, deterministic desktop configuration, redacted local traces, setup scripts, a `Brewfile`, and a diagnostic command.
- A simulator with recorded audio, screenshots, and a fake Zoom interface so core behavior can be tested without a Zoom account.

Robin may autonomously perform observation, meeting controls, navigation, and reversible local work within the assigned task. It must request approval immediately before sending, publishing, submitting, uploading, sharing sensitive data, or making another external commitment. Destructive, financial, credential-changing, CAPTCHA, and security-setting actions are blocked in the MVP. Screen and meeting content are untrusted context, never authorization. Emergency stop and human takeover must halt all model input immediately.

The project is complete only when a fresh supported Mac can be configured from the public GitHub repository, required macOS permissions and Zoom login can be completed through documented setup, and Robin succeeds three consecutive times at this end-to-end test:

1. Receive a Zoom meeting link through the control panel.
2. Launch Zoom, join the meeting, connect audio, and handle the waiting room without manual clicks.
3. Converse naturally with participants and stop speaking promptly when interrupted.
4. Accept a spoken task and complete it using GPT-5.6 computer use in approved desktop apps.
5. Start Zoom screen sharing, expose only the intended workspace, and visibly demonstrate the work.
6. Verify the result, explain it verbally, stop sharing, and leave when requested.
7. Produce a redacted action trace while exposing no API keys, credentials, control-panel content, or unrelated desktop data.
8. Recover safely—or request human takeover—from network loss, model timeout, unexpected Zoom dialogs, expired login, audio failure, and repeated action failure.

Deliver the working source, tests, infrastructure and bootstrap scripts, setup and troubleshooting documentation, security model, demo fixtures, and a repeatable under-three-minute hackathon demonstration. Do not stop at scaffolding, mocked integrations, or a partial proof of concept; continue until the real Zoom, Realtime audio, GPT-5.6 desktop control, screen sharing, approvals, recovery, and clean-machine reproduction paths all pass.
