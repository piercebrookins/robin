# Security model

## Trust boundaries

The owner’s authenticated control panel is authoritative. Meeting audio, Zoom chat, shared screens, websites, documents, email, and application content are untrusted context. They can inform work but cannot broaden Robin’s authority. The virtual meeting mix does not provide cryptographic speaker identity.

The dedicated Mac user, Zoom profile, display, and allow-listed applications form one isolation boundary. Robin is not intended to run on an operator’s personal desktop.

## Action classes

- Observation, meeting navigation, and reversible work inside the assigned local task are allowed and logged.
- Sending, publishing, submitting, uploading, exposing sensitive data, or making another external commitment pauses at the exact action and creates a short-lived owner approval.
- Destructive, financial, credential-changing, CAPTCHA, and security-setting actions are blocked in the MVP.
- Every mutating computer batch first declares its exact intent, target app, and risk. External or sensitive intent produces an approval that applies to one action, expires after two minutes in the panel, and must be consumed by the desktop worker within 30 seconds. Observe-only intent cannot authorize a mutating action.

## Stop semantics

Emergency stop and human takeover synchronously abort the Responses loop, stop all desktop execution, release held mouse buttons, stop queued audio, and prevent further model input. Resumption requires an explicit owner action. Repeated model, desktop, or verification failures enter takeover rather than continuing indefinitely.

## Data handling

OpenAI and control-panel secrets live in the login Keychain. The repository, command arguments, UI, screenshots, and traces never contain them. Trace files are mode `0600` in a mode `0700` directory and redact secret-shaped values, authorization fields, Zoom meeting passwords, task prose, exact-action prose, transcripts, summaries, briefings, and control-source payloads before disk write. Screenshots are transient model inputs and are not written to traces.

Before any screenshot is sent to GPT-5.6, the daemon checks all onscreen windows. Control-panel, credential, security-setting, and non-allow-listed application windows make the screenshot fail closed and request takeover.

The control panel is loopback-only, sends no-referrer/no-store/CSP headers, and uses a bearer token kept in session storage. For remote access, use a private network or an authenticated tunnel and terminate it outside the model-controlled display.

## Screen sharing

The production deployment uses a dedicated workspace display with no control panel, secret terminal, notifications, or personal applications. Robin selects the intended window/display through the normal Zoom share picker and verifies Zoom’s sharing state. Emergency stop clears sharing intent and attempts to stop Zoom through the UI; an owner should use takeover if Zoom is unresponsive.
