# Robin Agent

Robin is a Mac-hosted AI coworker prototype for joining Google Meet, listening for delegated work, generating analysis artifacts from a controlled workspace, and presenting results back to the meeting.

This repository implements the hackathon MVP described in `Robin_PRD.md` and `Robin_TDD.md`.

## Quick Start

After the one-time real-Meet setup, every rehearsal starts with one command:

```bash
make robin
```

The command prepares a clean rehearsal, launches Robin's dedicated Chrome profile, starts the
core and dashboard, opens the dashboard, and stays attached to the terminal until you press
Control-C. Paste the Meet link into the dashboard and choose **Join & listen**. Use
`scripts/run_robin.sh --keep-state` when you do not want to archive the previous rehearsal.
The live activity feed reports each audio stage. Under **System details and manual controls**,
use **Test Robin voice** and **Test hearing (4 sec)** before the first rehearsal.

One-time setup:

```bash
scripts/setup_partner.sh --real-meet --no-start
```

Open:

- Dashboard: http://127.0.0.1:3000
- Core API: http://127.0.0.1:8787/docs

For a faster install without tests or startup:

```bash
scripts/setup_partner.sh --skip-tests --no-start
```

For a provisioned Mac that is ready to exercise real Google Meet, Chrome, BlackHole, and the native bridge:

```bash
scripts/setup_partner.sh --real-meet
make launch-chrome
```

Chrome 136+ blocks remote debugging against the normal/default Chrome profile. `make launch-chrome` opens Robin's dedicated non-default Chrome profile with remote debugging enabled. Sign into Robin's Google account in that window once, leave it open, then run real Meet smoke tests.

## Useful Commands

```bash
make seed
make seed-demo
make setup
make launch-chrome
make dev
make doctor
make preflight
make test
make core
make web
make smoke
make smoke-test
make smoke-audio
make smoke-audio-live
make smoke-bridge
make smoke-capture
make smoke-listen
make smoke-leave-cleanup
make smoke-meet-fixture
make smoke-meet-recovery
make smoke-share-dialog-fixture
make smoke-calendar
make smoke-observability
make smoke-workspace
make smoke-retry-present
make smoke-validation
make smoke-clarification
make smoke-queue
make smoke-dedup
make smoke-agent
make smoke-browser-operator
make smoke-memory
make demo-reset
ROBIN_REAL_MEET_URL=https://meet.google.com/... make smoke-real-meet
```

`make smoke-capture` targets `com.google.Chrome` by default. On a machine where Chrome is not visible to ScreenCaptureKit, use:

```bash
uv run python scripts/smoke_capture.py --bundle-id com.apple.Safari
```

`make smoke-audio-live` is the complete local audio proof. It generates real OpenAI speech,
records and transcribes that speech directly from BlackHole 2ch, then plays the phrase from
Robin's signed-in Chrome, captures Chrome with ScreenCaptureKit, and transcribes the result.
The hearing check captures audio *coming out of
Chrome*—in a real Meet, speak from another participant or device. Speaking into the same Mac's
physical microphone is not Chrome output and is therefore not a valid hearing test.

`make preflight` checks demo readiness: API keys, workspace data, database writes, free disk, internet access, dashboard reachability, presentation URL configuration, browser mode, audio bridge mode, and BlackHole requirements. Simulator mode reports real Google login, Chrome UI control, and macOS capture permissions as not required; switch `browser.automation_mode` and `audio.bridge_mode` in `config/robin.example.yaml` to exercise real-machine prerequisites.

## Current MVP Scope

- Local FastAPI control plane with persisted runtime, meeting, transcript, task, artifact, and health state.
- A bounded Responses API tool loop for real tasks: the model selects approved workspace files,
  reads only those sources through workspace-scoped tools, and submits a cited presentation and
  Markdown report. The runtime rejects unread or unapproved citations and validates the result
  before it can be presented. Simulator-only runs retain the deterministic finance fixture worker.
- A generated-file tool for creating and revising Markdown, text, JSON, and CSV outputs inside the
  active task directory. It forbids path traversal, source edits, executable types, and oversized
  content; audit records store filenames and byte counts rather than document contents.
- Demo-readiness preflight covering workspace files, database writes, disk headroom, internet, dashboard, renderer, browser, audio, and simulator-vs-real prerequisites.
- Supervisor command that starts core and web, waits for health checks, writes logs, and restarts crashed child processes.
- Workspace boundary enforcement for CSV, XLSX, and PDF files.
- Deterministic business-analysis worker that creates chart JSON/PNG, a browser-renderable deck JSON, and a downloadable PPTX export.
- PDF context extraction for supporting narrative, citations, and validation source lineage while structured CSV/XLSX remains the numeric source of truth.
- Persisted validation reports for both general-agent and simulator finance outputs, with runtime
  gating before a deck can become ready to present.
- Revisioned chart, deck, and validation artifacts so spoken follow-ups preserve prior outputs while the presentation route serves the latest successful revision.
- Dashboard with meeting controls, health, transcript, task queue, artifacts, and emergency stop.
- Calendar discovery panel for configured local `.ics` or JSON events with Google Meet links.
- Calendar auto-join toggle and runtime poller that joins non-conflicted events inside the configured early window and leaves when the event ends.
- Workspace reindex and file-inspection API/dashboard panel for approved CSV, XLSX, and PDF source files.
- Structured event envelopes, metrics endpoint, event WebSocket, and JSONL traces for meeting/task/presentation activity.
- Demo reset command that archives generated/session state, reseeds fixtures, and restarts the local supervisor.
- Task retry and presentation stop controls for operator recovery during demos.
- Spoken task-failure blocker announcements before Robin returns to listening.
- Clarification flow for ambiguous implied requests with visible awaiting-clarification task state before Robin starts work.
- Deck-based presentation narration that advances through slides, speaks key findings, and stops presenting when complete.
- Explicit queued task state when concurrency slots are exhausted, including queued-task cancellation.
- Duplicate task suppression for repeated direct requests and transcript commands while work is active.
- Presentation renderer at `/present/[taskId]`.
- Google Meet browser adapter and audio bridge contracts with simulator-safe implementations.
- Playwright Meet-control smoke against a local fixture using a persistent Chrome profile.
- Bounded Meet UI recovery that refocuses the Meet tab, retries transient click failures, and captures diagnostic screenshots.
- Hybrid presentation automation: Playwright handles Meet DOM controls, while Codex/macOS Computer Use is restricted to Chrome's native share picker.
- Before sharing, Robin waits for the presentation readiness marker, verifies the expected task and revision, rejects renderer errors, and saves a presentation evidence screenshot.
- Native picker automation pins actions to Robin Chrome's loopback debugging PID, selects the uniquely titled `Robin Presentation` tab, verifies the picker closes, and persists screenshots plus a JSONL action trace.
- `make smoke-share-dialog-fixture` runs a localhost hybrid rehearsal: Playwright drives fake Meet DOM controls, the real Chrome picker opens through `getDisplayMedia()`, and Codex Computer Use completes and verifies the native dialog without contacting Google Meet.
- OpenAI-backed intent classification when `OPENAI_API_KEY` is available, with a local classifier fallback for offline tests.
- OpenAI-backed TTS and audio-file transcription smokes, with simulator mode for repeatable local tests.
- Basic speech floor manager that waits for a configurable silence window before Robin speaks, while ignoring Robin echo transcripts.
- Swift macOS bridge JSON command contract with Python process client and simulator client.
- Native bridge permission checks for Screen Recording, Accessibility, microphone, and BlackHole.
- Native bridge WAV playback routed to the exact configured BlackHole audio device, with playback
  failures, route, duration, and device reported truthfully.
- Native bridge ScreenCaptureKit app listing and bounded Chrome audio sample capture command.
- Bounded audio listening loop that captures, transcribes, deduplicates, and ingests meeting audio as transcript segments.
- Realtime transcription sessions with server VAD and incremental transcript deltas, plus graceful
  barge-in that stops Robin's native playback when another participant begins speaking.
- Durable, sourced meeting memory for topics, references, decisions, objections, questions,
  commitments, corrections, owners, deadlines, and resolution state. Memory survives restarts and
  is bounded before it is sent back to a model.
- Approval-gated model browser operator. GPT-5.6 inspects semantic Playwright snapshots and chooses
  bounded click/fill actions; joins, sends, submissions, sharing, permission changes, destructive
  actions, and other consequential controls require an exact action-bound confirmation token.
- Secret redaction at transcript, event, trace, browser-request, and workspace-context boundaries.
- Enforced peak-memory and workspace-disk budgets, displayed live in the dashboard.
- Meeting leave cleanup that stops the listening loop and presentation state before returning Robin to ready.

## Operator Evidence and Limitations

Robin distinguishes automated evidence from real-meeting proof:

- `make test` runs unit, integration, recovery, adversarial, long-context, and safety checks.
- `make smoke-agent`, `make smoke-browser-operator`, and `make smoke-memory` exercise live model
  tool use, semantic browser inspection, and sourced memory correction.
- `make smoke-audio-live` proves BlackHole output and Chrome capture/transcription locally.
- `make smoke-real-meet` exercises the full Meet path, but a successful local process alone is not
  proof that another participant heard narration or saw the shared surface.

The product definition of done still requires three consecutive fresh-start rehearsals with
different tasks. Each rehearsal must have participant-side confirmation of bidirectional audio,
the correct shared surface, audible narration, grounded output, a live Q&A or revision, graceful
leave, restored browser/audio state, and persisted audit evidence. Speaker names remain best-effort
unless Meet caption metadata is available. Do not describe Robin as complete until those gates pass.

## Native Bridge Mode

```bash
swift build --package-path apps/macos-bridge
make smoke-bridge
```

To use the process bridge from `robin-core`, set:

```yaml
audio:
  bridge_mode: "process"
  bridge_executable: "./apps/macos-bridge/.build/debug/robin-macos-bridge"
```

Native ScreenCaptureKit audio routing and real Google Meet screen-share picker control are represented behind adapter interfaces so the app can run and test on a development machine before Mac provisioning is complete.

The dashboard's audio checks verify Robin's voice output and Chrome capture/transcription separately.
In simulator mode speech is an intentional short tone; real rehearsals must use `audio.mode: openai`
and `audio.bridge_mode: process`. The live listener rejects silent captures locally before calling
transcription and times out a stuck native bridge instead of wedging the meeting loop.

Verify two real model-driven tasks through the bounded general-agent path in an isolated copy of
the approved workspace:

```bash
make smoke-agent
```

For real Google Meet control, set `browser.automation_mode` to `playwright`, `browser.connection_mode` to `cdp`, and `browser.share_dialog_mode` to `cua_driver`. Point `browser.executable_path` at Google Chrome, then run `make launch-chrome` and sign in with Robin's pre-provisioned Google account in that dedicated profile. `cua-driver` must be on `PATH`, and CuaDriver.app needs Accessibility and Screen Recording permission. Computer Use is not used for ordinary Meet controls or credentials; it is bounded to Chrome-owned dialogs that Playwright cannot access.
Then run `ROBIN_REAL_MEET_URL=... make smoke-real-meet` to join, generate a validated deck, present it, stop sharing, and leave.

Calendar discovery is available through the local provider:

```yaml
calendar:
  enabled: true
  provider: "local"
  file_path: "./RobinWorkspace/source-data/calendar_demo.ics"
  auto_join: true
  join_early_seconds: 60
```

## Supervisor Logs

`make dev` writes child-process logs under `RobinWorkspace/sessions/logs/` and stops both services cleanly when interrupted.

Runtime traces are written as JSONL under `RobinWorkspace/sessions/traces/`. Recent events and aggregate counters are available from `/api/events`, `/api/metrics`, and `/ws/events`.

## LaunchAgent

On a pre-provisioned Mac, install Robin as a user LaunchAgent with:

```bash
scripts/install_launch_agent.sh
```

Remove it with:

```bash
scripts/install_launch_agent.sh uninstall
```
