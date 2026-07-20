# Audio Latency Implementation Plan

This plan delivers the presentation-audio latency work in two sequential increments on one feature branch. Increment 1 removes the dominant Google Meet microphone-setup delay. Increment 2 hides the remaining text-to-speech startup latency through bounded narration prefetching.

## Current baseline

The recorded eight-slide rehearsal took approximately 5 minutes 53 seconds:

- Actual narration: approximately 1 minute 43 seconds
- Dead time: approximately 4 minutes 10 seconds
- OpenAI time to first audio: approximately 0.4–1.6 seconds
- Repeated delay between utterances: approximately 33.5 seconds

The dominant delay occurs before each TTS request, in the repeated Meet route-preparation path:

1. Verify the BlackHole microphone.
2. Open Meet audio settings.
3. Verify or disable Studio Sound and Adaptive Audio.
4. Unmute Meet.
5. Start streaming TTS.
6. Play PCM through the native bridge and BlackHole.
7. Mute Meet.
8. Repeat for the next slide.

## Goals

- Prepare the Meet audio route once per healthy meeting session.
- Use one unmute/mute pair for an entire deck narration.
- Preserve interruption, barge-in, cleanup, and audit behavior.
- Prepare upcoming narration while the current slide is playing or screen sharing is starting.
- Reduce p95 inter-slide silence to less than one second.
- Start the first narration within two seconds of screen-sharing completion.

## Out of scope

- Replacing the OpenAI speech model.
- Generating a single audio file for the entire presentation.
- Persistent cross-presentation TTS caching in the initial implementation.
- Changing deck-generation or presentation-rendering behavior.

## 1. Add latency instrumentation

Instrument the existing path before changing its behavior so improvements and regressions are measurable.

### Files

- `apps/core/robin_core/runtime.py`
- `apps/core/robin_core/meeting/adapters/google_meet.py`
- `apps/core/robin_core/audio/bridge.py`
- `apps/core/robin_core/schemas.py`

### Events

Add structured timing events for:

- `speech.route_prepare.started`
- `speech.route_prepare.completed`
- `speech.unmute.started`
- `speech.unmute.completed`
- `speech.synthesis.started`
- `speech.first_audio`
- `speech.playback.started`
- `speech.playback.completed`
- `presentation.slide.started`
- `presentation.slide.completed`

Where applicable, include:

- Task ID
- Slide index
- Duration
- Whether audio was streamed, prefetched, or a fallback
- Route-preparation cache status
- Error details

Do not log full narration text beyond the existing bounded and redacted behavior.

### Acceptance criteria

- A rehearsal trace can attribute the time between slide navigation and playback to individual stages.
- Existing `time_to_first_audio_ms` reporting remains available.
- No sensitive or unbounded narration content is added to logs.

## 2. Increment 1: prepare the Meet audio route once

### 2.1 Track route readiness

Update `GoogleMeetAdapter` in `apps/core/robin_core/meeting/adapters/google_meet.py` with explicit cached readiness state:

```python
speech_route_ready: bool = False
```

Introduce:

```python
async def prepare_speech_route(self, force: bool = False) -> str: ...
def invalidate_speech_route(self) -> None: ...
```

`prepare_speech_route()` should:

1. Return immediately when the route is already valid.
2. Select and verify the configured BlackHole device.
3. Disable Studio Sound and Adaptive Audio when those controls are available.
4. Store the selected device name.
5. Mark the route ready only after all required checks succeed.
6. Emit route-preparation timing and cache-hit events.

Invalidate route readiness when:

- Navigating to a meeting
- Leaving a meeting
- Replacing the Meet page
- Recovering or reconnecting CDP
- A mute or unmute operation fails
- The configured microphone device changes

Change `unmute()` so it uses `prepare_speech_route()` rather than unconditionally reopening and inspecting Meet settings.

### 2.2 Add a presentation-level speech session

Refactor the speech lifecycle in `apps/core/robin_core/runtime.py` so microphone setup is separate from individual speech playback.

Add an async context manager conceptually equivalent to:

```python
async with self._presentation_speech_session(task_id):
    ...
```

The context manager should:

1. Acquire `_speech_lock` once.
2. Wait for the speech floor once.
3. Prepare the Meet route.
4. Unmute once.
5. Preserve the prior meeting state.
6. Mute and restore state in a `finally` block.

Move per-utterance responsibilities into a helper:

```python
async def _speak_during_session(
    self,
    text: str,
    *,
    task_id: UUID | None = None,
    slide_index: int | None = None,
) -> SpeechRecord:
    ...
```

This helper should:

- Set `_active_spoken_text` for echo and interruption handling.
- Play and persist the speech record.
- Clear `_active_spoken_text` in `finally`.
- Preserve interruption behavior.
- Avoid reacquiring `_speech_lock`.
- Avoid muting or unmuting.

Keep `_acknowledge()` for isolated acknowledgements, but implement it using the same lower-level speech primitives.

### 2.3 Change deck narration

Update `_narrate_deck()` to use one speech session for the entire deck:

```python
async with self._presentation_speech_session(task_id):
    for index, slide in enumerate(deck.slides):
        await self.navigate_presentation(task_id, "goto", index=index)
        await self._speak_during_session(
            narration,
            task_id=task_id,
            slide_index=index,
        )
```

The presentation should remain unmuted across slide transitions and mute once when narration completes, fails, is interrupted, or is cancelled.

Because BlackHole is the selected input, silence between clips should not expose the physical microphone.

### 2.4 Preserve barge-in

The runtime currently recognizes barge-in primarily while `MeetingState.SPEAKING`. Ensure the deck-level session preserves that behavior.

Preferred approach:

- Keep the presentation state as `PRESENTING`.
- Accept wake-word barge-in while `PRESENTING` when `_active_spoken_text` is set.

On interruption:

1. Stop native playback.
2. Stop remaining narration.
3. Cancel future prefetch work after Increment 2 is added.
4. Mute safely.
5. Leave the presentation and task in a consistent state.

### 2.5 Increment 1 tests

Add or update tests in:

- `apps/core/tests/test_google_meet_adapter.py`
- `apps/core/tests/test_runtime.py`

Required cases:

1. An eight-slide presentation causes one route preparation.
2. An eight-slide presentation causes one unmute and one final mute.
3. The microphone is muted after successful narration.
4. The microphone is muted after synthesis or playback failure.
5. The microphone is muted after cancellation.
6. Route preparation is repeated after page replacement or reconnect.
7. An isolated acknowledgement still prepares, unmutes, speaks, and mutes correctly.
8. Wake-word barge-in interrupts deck narration.
9. Presentation navigation and persisted speech records remain ordered.
10. A failed route preparation is not cached as successful.

### Increment 1 acceptance criteria

- No Meet settings interaction occurs between slides.
- Inter-slide preparation overhead is under 500 ms, excluding TTS startup.
- One unmute/mute pair is used for a complete deck.
- Existing presentation, cleanup, failure, and barge-in tests remain green.

## 3. Increment 2: bounded narration prefetching

### 3.1 Separate synthesis from playback

Refactor `AudioBridge` in `apps/core/robin_core/audio/bridge.py` into three operations:

```python
async def prepare_speech(self, text: str) -> PreparedSpeech: ...
async def play_prepared(self, prepared: PreparedSpeech) -> SpeechRecord: ...
async def speak(self, text: str) -> SpeechRecord: ...
```

Add an internal `PreparedSpeech` dataclass containing:

- Text
- Audio path
- Model
- Voice
- Format
- Byte count
- Audio duration
- Synthesis start and completion timing
- Preparation error, when applicable

Preparation must not create or persist a completed `SpeechRecord`. A speech record should enter runtime state only when playback starts or completes.

Retain `speak()` as the existing streaming path for isolated acknowledgements and prefetch fallback.

### 3.2 Generate narration text once

Before starting screen sharing, calculate all slide narration:

```python
narrations = [
    self._slide_narration(deck, index)
    for index in range(len(deck.slides))
]
```

The same strings must be used for preparation, playback, events, persistence, and fallback. This prevents narration from changing between preparation and delivery.

### 3.3 Add a bounded prefetch coordinator

Create:

```text
apps/core/robin_core/audio/prefetch.py
```

The coordinator should:

- Accept ordered narration items.
- Prepare audio through an `asyncio.Semaphore`.
- Preserve slide ordering independently of completion order.
- Expose `await get(slide_index)`.
- Record individual preparation failures without failing the entire deck.
- Cancel and await outstanding tasks during cleanup.
- Remove incomplete temporary files.

Add configuration to `PresentationConfig` in `apps/core/robin_core/config.py`:

```python
narration_prefetch_enabled: bool = True
narration_prefetch_concurrency: int = 2
```

Mirror these settings in `config/robin.example.yaml`.

Default concurrency should be two. Do not use unbounded `asyncio.gather()`.

### 3.4 Overlap preparation with screen sharing

Change `present_task()` ordering:

1. Load and validate the deck.
2. Calculate all narration strings.
3. Start bounded prefetch tasks.
4. Start renderer and screen-sharing setup while preparation continues.
5. Enter the deck-level speech session.
6. For each slide:
   1. Await its prepared audio.
   2. Navigate to the slide.
   3. Play immediately.
7. Cancel and await unused preparation tasks in `finally`.

Screen-sharing setup currently takes approximately 11 seconds, so starting preparation before sharing should make most or all narration ready before it is needed.

Do not navigate to a slide and then wait for its preparation. Await readiness first so the audience does not see a silent slide unnecessarily.

### 3.5 Provide a safe fallback

If preparation for a slide fails:

1. Emit `presentation.narration.prefetch_failed`.
2. Navigate to the slide.
3. Use the existing streaming `audio.speak(text)` path.
4. Continue with later prepared slides.

A single preparation failure must not discard the presentation or invalidate successfully prepared later slides.

### 3.6 Keep caching presentation-scoped initially

Use presentation-scoped prepared files for the initial implementation. Persistent content-addressed caching can be added later with a key such as:

```text
SHA-256(model + voice + format + normalized_text)
```

Deferring persistent caching avoids introducing stale-voice behavior, cache eviction, cleanup policy, and workspace-disk-budget concerns into the latency fix.

### 3.7 Increment 2 tests

Add or update tests in:

- `apps/core/tests/test_audio_bridge.py`
- `apps/core/tests/test_runtime.py`
- A new `apps/core/tests/test_audio_prefetch.py`, if the coordinator warrants focused tests

Required cases:

1. Prepared speech writes a valid playable WAV.
2. Preparation alone does not create a completed speech record.
3. Playback persists exactly one record.
4. Maximum concurrent synthesis requests never exceeds configuration.
5. Preparation begins while screen sharing is still starting.
6. Slides play in deck order even when preparation completes out of order.
7. Prefetch failure falls back to streaming.
8. Cancellation awaits all preparation tasks.
9. Partial temporary files are removed after failure or cancellation.
10. Interruption prevents subsequent slides from playing.
11. Simulator mode exercises the same orchestration path.
12. Disabled prefetch uses the existing streaming behavior.

### Increment 2 acceptance criteria

- First narration starts within two seconds of screen-sharing completion.
- Inter-slide silence p95 is less than one second.
- Narration order remains deterministic.
- No orphan tasks or partial audio files remain after cancellation.
- API concurrency never exceeds the configured bound.
- A prefetch failure degrades to streaming without stopping the deck.

## 4. Commit and delivery sequence

Keep the increments in separate commits so Increment 1 can be reviewed and retained independently if prefetching needs additional tuning.

### Commit 1: instrumentation

- Add stage-level events and metrics.
- Add trace assertions.
- Establish the pre-change latency baseline.

### Commit 2: deck-level speech session

- Cache Meet route readiness.
- Add route invalidation.
- Unmute once per deck.
- Preserve barge-in and cleanup.
- Add Increment 1 tests.

### Commit 3: synthesis/playback separation

- Add `PreparedSpeech`.
- Add `prepare_speech()` and `play_prepared()`.
- Preserve `speak()` as the streaming fallback.
- Add audio-bridge tests.

### Commit 4: bounded presentation prefetch

- Add the coordinator.
- Overlap preparation with screen sharing.
- Add fallback, cancellation, and cleanup.
- Add Increment 2 tests and configuration.

### Commit 5: rehearsal evidence

- Run the full verification suite.
- Record before-and-after timings.
- Update operational documentation if behavior or configuration changed.

## 5. Verification

Run the automated suite:

```bash
make test
make smoke-audio
make smoke-audio-live
make smoke-retry-present
make smoke-conversation-revision
```

Then conduct three real-Meet rehearsals using decks with at least six slides.

Capture:

- Total presentation time
- Total spoken duration
- First-narration latency
- Per-slide transition latency
- Route-preparation count
- Mute and unmute count
- Prefetch success and fallback count
- Interruption and cleanup results

## 6. Definition of done

- One successful microphone-route preparation occurs per healthy meeting session.
- One unmute/mute pair is used per deck.
- No 30-second Meet-settings pauses occur between slides.
- First narration begins within two seconds of sharing completion.
- p95 slide transition is below one second.
- Slide and narration ordering remains correct.
- Wake-word barge-in still stops playback.
- Failure and cancellation always restore the muted state.
- No prefetch task or partial audio file survives cleanup.
- Three consecutive real-Meet rehearsals receive participant-side confirmation that the presentation was visible and every narration segment was audible.

## 7. Implementation evidence

Implemented on branch `codex/audio-latency` in worktree `/Users/vasu/code/robin-audio-latency`.

### Commits

- `7f22125` Add audio latency instrumentation.
- `456c50d` Cache Meet speech route for deck narration.
- `9b07284` Split speech synthesis from playback.
- `064e755` Prefetch presentation narration with bounded concurrency.

### Automated verification

- `make test`: passed after installing workspace JavaScript dependencies with `pnpm install`.
  - Python: 151 passed, 5 warnings.
  - Web: 1 passed.
- `make smoke-retry-present`: passed.
- `make smoke-conversation-revision`: passed.
- Focused audio latency suite passed:
  - `apps/core/tests/test_google_meet_adapter.py`
  - `apps/core/tests/test_audio_prefetch.py`
  - `apps/core/tests/test_audio_bridge.py`
  - `apps/core/tests/test_runtime.py`

### Environment-gated verification

- `make smoke-audio`: not completed in this shell because `OPENAI_API_KEY` is unset while `config/robin.example.yaml` uses `audio.mode=openai`.
- `make smoke-audio-live`: not completed for the same missing `OPENAI_API_KEY` prerequisite.
- Three real-Meet rehearsals were not run from this coding environment; they still require a configured OpenAI key, native bridge, BlackHole route, browser profile, and participant-side confirmation.
