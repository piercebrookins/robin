# Robin Production Audio Implementation Plan

## 1. Objective

Replace Robin's simulated audio path with a real, deterministic, observable two-way audio system for Google Meet:

1. Robin continuously hears the dedicated Chrome instance without recording the physical microphone.
2. Finalized participant speech is transcribed and enters the existing intent pipeline.
3. Robin synthesizes real speech and sends it only through its virtual microphone.
4. Google Meet selects and verifies Robin's dedicated microphone and speaker devices on every join.
5. Join, leave, disconnect, and emergency-stop operations own the complete audio lifecycle.
6. Missing devices, silent capture, failed playback, and disconnected transcription are reported as failures rather than healthy states.

The intended Mac routing contract is:

```text
Remote participants
  -> Google Meet / dedicated Robin Chrome
  -> ScreenCaptureKit application-audio capture
  -> Swift bridge (48 kHz source -> 24 kHz mono PCM16)
  -> OpenAI realtime transcription session
  -> finalized transcript segments
  -> Robin intent/task runtime

Robin speech text
  -> OpenAI streaming TTS (24 kHz mono PCM)
  -> Swift bridge playback to exact CoreAudio device UID
  -> Robin Microphone / BlackHole loopback
  -> Google Meet selected microphone
  -> remote participants

Google Meet selected speaker
  -> Robin Speaker / isolated BlackHole loopback
  -> no physical speaker monitoring by default
```

## 2. Definition of Done

The production audio project is complete only when all of the following are true:

- Joining a real Meet automatically configures audio devices and starts listening after admission.
- A second Meet participant can speak naturally and see a finalized transcript appear without using the dashboard transcript injection endpoint.
- Robin can reply with intelligible synthesized speech that the second participant hears.
- Robin's speech is not emitted from the Mac's physical speakers.
- No capture gaps are deliberately introduced between chunks.
- Missing or mismatched device UIDs prevent a false healthy state.
- Robin is muted whenever it is not actively playing speech.
- A TTS, playback, browser, or cancellation failure still re-mutes Robin in a `finally` path.
- Leave and emergency stop halt capture, transcription, and playback within two seconds.
- Robin's own speech does not create a new task.
- A 30-minute two-client Meet soak completes without an orphaned capture stream, bridge process, or transcription session.

Suggested performance targets:

- Capture stream ready within 5 seconds after Meet admission.
- Final transcript emitted within 1.5 seconds of detected speech end under normal network conditions.
- TTS audio begins within 1.5 seconds of a speak request.
- Audio-frame loss remains below 0.5% during a 30-minute soak.
- Zero successful playback responses that used an unconfigured fallback device.

## 3. Configuration and Domain Model

### 3.1 Stop using one audio mode for unrelated behavior

Replace the current `audio.mode` and `audio.bridge_mode` combination with explicit provider and routing sections. Keep simulation available for tests, but make mixed real/simulated operation deliberate.

Proposed shape:

```yaml
runtime:
  deployment_mode: "real" # real | simulator

audio:
  capture:
    provider: "screen_capture_kit" # screen_capture_kit | fixture
    bundle_id: "com.google.Chrome"
    sample_rate: 24000
    channels: 1
    encoding: "pcm_s16le"
    frame_duration_ms: 100
  transcription:
    provider: "openai_realtime" # openai_realtime | fixture
    model: "gpt-realtime-whisper"
    language: "en"
    vad_threshold: 0.5
    prefix_padding_ms: 300
    silence_duration_ms: 500
  speech:
    provider: "openai" # openai | tone_fixture
    model: "gpt-4o-mini-tts"
    voice: "alloy"
    response_format: "pcm"
    cooldown_ms: 700
  bridge:
    provider: "process" # process | simulator
    executable: "./apps/macos-bridge/.build/debug/robin-macos-bridge"
  routing:
    tts_output_device_uid: "com.robin.audio.microphone"
    meet_microphone_label: "Robin Microphone"
    meet_speaker_label: "Robin Speaker"
    allow_default_output_fallback: false
```

The exact TTS output UID must be verified against the device graph during implementation. The important invariant is that the Swift playback target and Meet microphone resolve to the same loopback path.

### 3.2 Validate configurations at startup

Add Pydantic literals/enums and a cross-field validator in `apps/core/robin_core/config.py`:

- `deployment_mode=real` rejects fixture/simulator providers.
- Realtime transcription requires 24 kHz, mono, signed 16-bit little-endian PCM.
- Process bridge requires an executable path.
- Real routing requires device UIDs and Meet-visible labels.
- Default-device fallback is forbidden in real mode.
- Unknown providers fail configuration loading instead of silently choosing a simulator.

Create a committed `config/robin.real.example.yaml` and keep the current demo configuration explicitly named as a simulator configuration. Do not mutate a shared example file with string replacement during setup.

### 3.3 Add runtime audio state

Add an `AudioRuntimeState` model to `schemas.py` containing at least:

- capture state and session ID;
- capture bundle ID and resolved application PID;
- input format;
- last frame timestamp, sequence number, RMS level, and dropped-frame count;
- transcription connection state and last final-transcript timestamp;
- playback state, speech ID, and resolved output-device UID/name;
- Meet-selected microphone and speaker labels;
- last error and last successful probe time.

Expose this state in `RuntimeSnapshot` and the dashboard.

## 4. Workstream A: Persistent Native Bridge

Affected files:

- `apps/macos-bridge/Sources/RobinBridge/main.swift`
- `apps/core/robin_core/audio/bridge_client.py`
- `apps/core/robin_core/audio/bridge.py`
- `apps/macos-bridge/Package.swift`

### 4.1 Make the bridge a long-lived process

The current Python client starts a new process for every command. Replace it with one supervised bridge process per Robin core process.

Initial transport:

- newline-delimited JSON commands on stdin;
- newline-delimited responses and events on stdout;
- request IDs for command correlation;
- Base64 audio payloads for the first working streaming version;
- a bounded Python queue between stdout parsing and the transcription client.

At 24 kHz mono PCM16, 100 ms contains 4,800 bytes. Base64 JSON at ten frames per second is acceptable for the first production milestone. If profiling shows pressure, replace only the frame transport with a Unix-domain socket and length-prefixed binary messages without changing the control protocol.

Proposed protocol additions:

```json
{"id":"1","method":"audio.capture.start","params":{"bundle_id":"com.google.Chrome"}}
{"type":"audio.capture.frame","capture_id":"...","sequence":1,"captured_at_ms":0,"sample_rate":24000,"channels":1,"encoding":"pcm_s16le","rms":0.08,"data_base64":"..."}
{"id":"2","method":"audio.capture.stop","params":{"capture_id":"..."}}

{"id":"3","method":"audio.output.begin","params":{"speech_id":"...","device_uid":"...","sample_rate":24000,"channels":1,"encoding":"pcm_s16le"}}
{"id":"4","method":"audio.output.chunk","params":{"speech_id":"...","sequence":1,"data_base64":"..."}}
{"id":"5","method":"audio.output.end","params":{"speech_id":"..."}}
{"type":"audio.output.drained","speech_id":"..."}
{"id":"6","method":"audio.output.cancel","params":{"speech_id":"..."}}
```

### 4.2 Implement real capture start and stop

Replace the no-op start/stop handlers with an owned `SCStream`:

- resolve the dedicated Chrome application by exact bundle ID;
- fail if zero or multiple ambiguous capture applications are found;
- create one `SCContentFilter` and `SCStream` per meeting capture session;
- capture audio continuously;
- register stream errors and stop notifications;
- make repeated start/stop calls idempotent;
- restart once after a recoverable ScreenCaptureKit failure;
- emit explicit started, stopped, and failed events.

Do not report a successful capture until at least one non-empty sample buffer has arrived.

### 4.3 Normalize audio in Swift

Use `AVAudioConverter` or an equivalent Core Audio converter to:

- accept ScreenCaptureKit's native 48 kHz stereo buffers;
- downmix to mono;
- resample to 24 kHz;
- convert to signed PCM16 little-endian;
- produce 100 ms frames;
- retain sequence and capture timestamps;
- calculate peak and RMS levels;
- count samples dropped because of queue pressure.

Never perform blocking JSON writes on the ScreenCaptureKit callback queue. Copy into a bounded processing queue first.

### 4.4 Resolve output devices exactly

Return structured devices from `audio.devices.list`:

- CoreAudio device ID;
- stable UID;
- display name;
- input/output channel counts;
- sample rate;
- transport type;
- default input/output flags.

Select playback by exact UID. Remove substring matching and remove the fallback to `AVAudioPlayer` on the system default device in real mode.

### 4.5 Stream and cancel playback

Maintain one `AVAudioEngine` playback session:

- bind the engine to the exact output-device UID;
- accept ordered PCM chunks;
- reject duplicate or out-of-order sequence numbers;
- schedule buffers without gaps;
- emit `drained` only after the final buffer finishes;
- support immediate cancellation;
- stop and release the engine on shutdown.

## 5. Workstream B: Realtime Transcription

Add modules:

- `apps/core/robin_core/audio/transcription.py`
- `apps/core/robin_core/audio/audio_session.py`

### 5.1 Create a transcription client abstraction

Define a protocol with:

- `connect(meeting_id)`;
- `append_frame(frame)`;
- `events()` asynchronous iterator;
- `close()`;
- connection-health properties.

Implement both `OpenAIRealtimeTranscriber` and `FixtureTranscriber`. Tests should inject the fixture explicitly; production must never choose it because an API request failed.

### 5.2 Open one transcription session per meeting

Configure a transcription-only realtime session with:

- 24 kHz mono PCM input;
- the configured transcription model and language;
- server VAD;
- prefix padding and silence duration from configuration;
- no automatic conversational response generation.

Forward only finalized/stabilized transcript segments into `runtime.ingest_transcript`. Partial deltas may update the dashboard but must not create tasks.

### 5.3 Bound buffers and recovery

- Use a bounded frame queue sized for approximately two seconds of audio.
- Record and expose drops; do not silently grow memory.
- Reconnect with bounded exponential backoff.
- During reconnect, retain no more than two seconds of recent audio.
- Create a new session after the documented session limit or a terminal server event.
- Deduplicate final segments by server item ID plus normalized text, not text alone.
- Preserve server/capture timing metadata when constructing `TranscriptSegment`.

### 5.4 Self-speech handling

Record playback intervals and the exact synthesized text. While Robin speaks:

- continue capturing other participants;
- mark overlapping transcripts as possible echo;
- suppress a candidate only when timing overlap and text similarity both indicate Robin's speech;
- retain interrupted participant speech;
- apply the configured post-speech cooldown before accepting a highly similar command.

## 6. Workstream C: Streaming Speech Output

### 6.1 Separate synthesis from playback

Refactor `AudioBridge.speak` into:

- a speech synthesizer that yields PCM chunks;
- a playback sink that routes chunks to the bridge;
- a coordinator that records speech state and completion.

Use the OpenAI SDK's streaming speech response with PCM for the primary path. Retain WAV generation only as an artifact/debug option.

### 6.2 Make speaking failure-safe

Refactor `RobinRuntime._acknowledge` into an explicit sequence:

1. Wait for the speech floor.
2. Record playback intent and enter `SPEAKING`.
3. Unmute Meet and verify its state.
4. Begin bridge playback.
5. Stream TTS chunks.
6. End and wait for `drained`.
7. In `finally`, cancel unfinished playback, mute Meet, record the result, and restore state.
8. Apply the cooldown.

If unmute verification fails, do not send audio. If playback fails, re-mute before propagating the error.

### 6.3 Emergency cancellation

Emergency stop must cancel:

- the active OpenAI speech response;
- pending PCM forwarding;
- Swift playback;
- the speech floor wait;
- any pending acknowledgement.

## 7. Workstream D: Google Meet Audio Device Control

Affected files:

- `apps/core/robin_core/meeting/selectors.py`
- `apps/core/robin_core/meeting/adapters/google_meet.py`
- `apps/core/robin_core/browser/page_driver.py`
- `apps/core/robin_core/browser/controller.py`

### 7.1 Extend the page-driver contract

Add operations needed to inspect a settings dialog:

- read visible text/value from the first matching locator;
- click an exact option by accessible name;
- query checked/selected state;
- wait for text/value equality;
- collect a bounded diagnostic snapshot on failure.

Implement these for both Playwright and the fixture driver.

### 7.2 Add resilient Meet settings selectors

Add selector candidates for:

- settings button;
- audio tab;
- microphone selector and current value;
- speaker selector and current value;
- exact device options;
- close/done button;
- microphone permission/device error banners.

Prefer roles and accessible names, then localized text candidates, then carefully scoped structural selectors. Avoid positional selectors.

### 7.3 Configure and verify before joining

Add `configure_audio_devices()` to `GoogleMeetAdapter`:

1. Open settings from prejoin.
2. Select the configured microphone label.
3. Select the configured speaker label.
4. Read both selected values back.
5. Save them in audio runtime state.
6. Close settings.
7. Fail admission readiness if either value is wrong.

Keep the microphone muted through the operation.

### 7.4 Browser ownership decision

Short term, retain the dedicated manually launched Chrome profile and CDP attachment to minimize scope. Add explicit checks that the default context and expected profile are present. Playwright documents CDP attachment as lower fidelity than a native Playwright connection, so avoid relying on advanced context mutation through CDP.

After audio is stable, evaluate moving browser ownership into `launch_persistent_context` with the dedicated profile and remote-debugging port. That would remove the external launch ordering problem while preserving the CuaDriver PID lookup.

## 8. Workstream E: Meeting Lifecycle Integration

Update `RobinRuntime.join_meeting`:

1. Run live bridge and routing preflight.
2. Open Meet and configure devices.
3. Join muted.
4. Confirm admission.
5. Connect realtime transcription.
6. Start native capture.
7. Wait for the first valid frame and transcription readiness.
8. Enter `LISTENING` and publish health.

If steps 5-7 fail, leave the meeting or enter an explicit degraded state according to configuration. Do not claim `LISTENING` while capture is absent.

Update leave/disconnect cleanup in this order:

1. Mute Meet.
2. Cancel speech playback.
3. Stop presentation.
4. Stop ScreenCaptureKit.
5. Close transcription.
6. Leave Meet.
7. Close meeting pages and clear session state.

Make cleanup idempotent so emergency stop and ordinary leave can safely race.

## 9. Workstream F: Truthful Health and Observability

### 9.1 Replace optimistic health initialization

Do not initialize capture and virtual-mic health to true. Derive health from live probes.

Required checks:

- native bridge process alive and protocol version compatible;
- Screen Recording permission;
- dedicated Chrome application visible to ScreenCaptureKit;
- exact TTS output UID available with output channels;
- configured Meet microphone and speaker labels visible and selected;
- capture stream receiving recent, non-empty frames;
- transcription session connected;
- playback engine bound to the configured UID;
- Meet muted when playback is inactive.

The `/health` endpoint's top-level `ok` must equal the aggregate required health state.

### 9.2 Metrics and events

Add counters/gauges for:

- audio frames received and dropped;
- capture restarts;
- current and recent RMS;
- transcription connects, reconnects, partials, finals, and failures;
- transcript end-to-final latency;
- TTS request-to-first-byte latency;
- playback duration, drains, cancellations, and failures;
- Meet device-selection failures;
- suppressed self-echo candidates.

Do not log raw audio or API credentials. Make transcript logging configurable for privacy.

### 9.3 Dashboard

Replace the single capture status with separate cards/meters for:

- Meet routing;
- native capture;
- live input level;
- transcription connection;
- playback routing;
- current speaking state;
- last audio error.

Keep manual capture and transcript injection controls under a clearly marked development section.

## 10. Testing Strategy

### 10.1 Swift unit tests

- exact UID selection chooses the requested device when 2ch and 16ch devices coexist;
- missing UID fails without default fallback;
- stereo 48 kHz input converts to mono 24 kHz PCM16;
- frame sequence and timestamps are monotonic;
- silence and non-silence RMS are classified correctly;
- start/stop are idempotent;
- ordered playback drains;
- cancellation stops scheduled playback.

Extract CoreAudio enumeration and audio sinks behind protocols so most tests do not require physical devices.

### 10.2 Python unit tests

- bridge process correlates concurrent responses by ID;
- malformed bridge events fail safely;
- bounded queues record dropped frames;
- only finalized transcripts enter intent classification;
- reconnect logic is bounded;
- self-speech similarity requires timing overlap;
- `_acknowledge` always mutes in `finally`;
- emergency stop cancels active playback and transcription;
- real configuration rejects simulator providers.

### 10.3 Meet fixture tests

Extend the fake Meet page with an audio settings dialog that supports:

- selectable microphone/speaker options;
- persisted selected labels;
- missing-device and permission-error states;
- an assertion-visible mute state.

Test selection, verification, changed labels, failure screenshots, mute restoration, and join refusal.

### 10.4 Hardware integration tests

Add scripts that:

- enumerate and assert the exact Robin device UIDs;
- play a known phrase into the virtual microphone and verify it on the corresponding input side;
- play a known browser audio fixture and require nonzero ScreenCaptureKit frames;
- transcribe the fixture and compare normalized text;
- prove physical speakers remain unused.

### 10.5 Real two-client Meet test

Replace the current real-Meet smoke with a test that requires a second participant/client:

1. Robin joins muted with verified devices.
2. Client B says a randomized phrase.
3. Robin captures and finalizes a matching transcript.
4. Robin speaks a randomized reply.
5. Client B records or confirms the reply.
6. Robin returns to muted/listening.
7. Robin leaves and all audio components stop.

Persist timestamps, state transitions, device identities, and pass/fail evidence. Do not treat task/deck creation alone as an audio pass.

## 11. Delivery Sequence

### PR 1: Configuration and exact routing

- New configuration models and real example config.
- Structured CoreAudio device enumeration.
- Exact UID selection.
- Removal of default-output fallback.
- Truthful device preflight.

Exit gate: the bridge selects the intended Robin loopback UID even with both BlackHole variants installed.

### PR 2: Meet device selection

- Page-driver inspection methods.
- Meet settings selectors and fixture.
- Select-and-verify flow.
- Routing state in snapshots/dashboard.

Exit gate: fixture and real prejoin both prove the selected labels before admission.

### PR 3: Correct batch end-to-end audio

- Real OpenAI TTS/STT configuration.
- Automatic join/listen/leave lifecycle.
- Nonzero capture validation.
- Failure-safe mute and cooldown.
- Two-client batch smoke.

Exit gate: Robin hears and answers a second participant without dashboard transcript injection.

### PR 4: Persistent capture bridge

- Long-lived bridge process.
- Real capture start/stop.
- PCM conversion and frame events.
- Buffer/drop metrics.

Exit gate: continuous fixture playback produces a gap-free ordered frame stream for 30 minutes.

### PR 5: Realtime transcription

- Realtime transcription client.
- Server VAD, partial/final events, reconnect logic.
- Partial dashboard transcript.
- Final-only intent ingestion.

Exit gate: natural speech finalizes within the latency target and reconnects without duplicate tasks.

### PR 6: Streaming and cancellable TTS

- Streaming PCM synthesis.
- Chunked native playback and drained event.
- Emergency cancellation.
- Playback-aware echo suppression.

Exit gate: reply audio begins within the latency target and every error/cancellation path restores mute.

### PR 7: Operational hardening

- Full health aggregation.
- Dashboard audio telemetry.
- Soak and fault-injection tests.
- Updated setup/runbooks.

Exit gate: all Definition of Done criteria pass on the provisioned Mac.

## 12. Rollback and Migration

- Preserve the simulator implementations for CI, but require explicit dependency injection or a simulator config.
- Keep the batch transcription endpoint during realtime rollout as a diagnostic tool, not an automatic production fallback.
- Feature-flag realtime capture until the two-client test passes reliably.
- If realtime transcription fails during a meeting, expose a degraded state and allow an operator-approved switch to bounded batch capture; do not silently substitute a fixed transcript.
- Version the bridge protocol so core and native binaries fail clearly when mismatched.

## 13. Non-Audio Simulation and Production-Wiring Audit

### Explicit simulators enabled in the current configuration

1. **Audio synthesis/transcription and native bridge**: both audio modes are simulator-backed.
2. **Native share picker**: `share_dialog_mode` is `simulator`, so it reports a successful selection and closed picker without operating the real dialog.
3. **Development transcript injection**: the dashboard directly posts typed text into the transcript endpoint.

### Real adapters exist, but production usage is incomplete or disabled

1. **Google Meet control**: the Playwright adapter is real and currently configured, but depends on an externally launched CDP Chrome. The application does not own Chrome startup, and current tests primarily exercise a fake Meet page.
2. **Screen sharing**: a real CuaDriver implementation exists, but the active configuration selects the simulator. The real smoke test does not prove the second participant receives the shared presentation.
3. **Intent classification**: OpenAI classification is real when available, but every exception silently falls back to a small keyword classifier, hiding model outages and materially changing behavior.
4. **Calendar auto-join**: scheduling logic is real, but the only provider reads local ICS/JSON files. There is no Google Calendar or Microsoft calendar API integration.

### Production-shaped interfaces that are placeholders

1. **Native bridge UI commands**: `ui.find` always returns an empty array and `ui.press` always reports success without interacting with UI.
2. **Native screen capture**: the `application` parameter is ignored; the command captures the entire main display.
3. **Health reporting**: browser health is initialized as healthy, capture health starts healthy, permission preflight contains informational successes rather than live verification, and `/health` always returns top-level `ok: true`.
4. **Audio capture lifecycle**: native start/stop commands report state without owning a stream.

### Real code that is still a hard-coded MVP workflow

1. **Task execution**: every accepted task runs `run_finance_analysis`, regardless of the requested work.
2. **Analysis schema**: the worker requires quarter, revenue, and operating-income columns and produces hard-coded 2024 quarterly titles and metrics.
3. **File selection**: workspace search is token overlap, then the worker chooses the first CSV; it is not a general planning/tool-use system.
4. **PDF usage**: PDF context is reduced to a short leading text excerpt and is not deeply analyzed.
5. **Deck generation and narration**: output uses one fixed finance presentation template and deterministic narration patterns.

### Missing rather than simulated

1. Meet caption ingestion and participant speaker attribution are not implemented; captured speech is labeled `Meeting audio`.
2. Participant count and robust meeting-disconnect detection are absent.
3. General-purpose computer work/tool execution is absent; Robin cannot perform arbitrary delegated tasks.
4. Cloud document sources and calendar providers are absent by MVP design.
5. Production authentication, multi-user isolation, remote deployment, and secrets management are absent; the system is a trusted single-Mac local service.

## 14. Recommended Follow-on Priorities

After production audio, address the remaining gaps in this order:

1. Enable and validate the real share-dialog path.
2. Replace the universal finance worker with a capability-routed task executor.
3. Add a real calendar provider and durable OAuth handling.
4. Make health and preflight fail on live dependency failures.
5. Add captions/speaker attribution and meeting-disconnect detection.
6. Decide whether Robin remains a trusted single-user Mac application or needs authentication and multi-user boundaries.
