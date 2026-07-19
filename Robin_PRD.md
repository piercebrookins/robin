# Robin Product Requirements Document

**Product:** Robin  
**Version:** 0.2
**Status:** Active implementation; real-Meet completion gates not yet satisfied
**Platform:** Dedicated macOS host  
**Primary meeting platform:** Google Meet  
**Document type:** Product Requirements Document  

---

## 1. Product Summary

Robin is an autonomous AI coworker that joins online meetings as an independent participant.

Robin runs on its own dedicated Mac, joins Google Meet using its own account, listens to the meeting, understands shared conversational context, accepts requests from any participant, completes work using files and applications available on its machine, and returns to the meeting to speak and present the result.

Robin is designed to behave like a human employee rather than a private meeting assistant. It has its own meeting identity, microphone, browser session, computer workspace, task queue, voice, and screen-sharing capability.

The current implementation targets open-ended, workspace-grounded requests during a live meeting.
In real partner mode, GPT-5.6 chooses among bounded workspace tools, reads approved CSV,
Excel, PDF, PowerPoint, Markdown, and text sources, and submits a cited presentation and report.
Finance-specific deterministic generation remains only as the offline simulator fixture.

### 1.1 Current Evidence and Remaining Product Gaps

Implemented and automated as of July 19, 2026:

- Model-directed workspace listing, source reading, and cited deliverable submission.
- Runtime enforcement of workspace containment, read-before-cite, iteration limits, and validation.
- Non-finance customer-feedback and launch-readiness task evaluations.
- Real API and full-runtime generation of grounded multi-source briefings.
- Google Meet join/listen/present automation, BlackHole speech routing, loopback audio proof,
  native screen-share dialog control, persisted events, and live dashboard activity.
- Realtime transcription with server VAD, incremental deltas, and interruption-driven playback stop.
- Streaming PCM speech into BlackHole with first-audio timing, partial WAV audit retention, and route restoration.
- Meet-caption speaker labels merged with realtime STT when a trustworthy text match exists.
- Durable sourced meeting memory with correction/resolution semantics and restart persistence.
- A model-directed semantic browser loop with exact, action-bound approvals for consequential UI,
  including workspace-scoped uploads and isolated, auditable downloads.
- Bounded creation and revision of Markdown, text, JSON, and CSV task outputs in isolated generated directories.
- Secret redaction, prompt-injection boundaries, bounded model context, and enforced memory/disk budgets.
- Dashboard views for hearing, speech, beliefs, tool actions, confirmation waits, and resource use.
- Persisted working, awaiting-confirmation, blocked, failed, verified, and cancelled task outcomes.
- Validated-artifact context for grounded source Q&A before and after a narrated presentation.
- A tested emergency stop that interrupts speech and halts capture, work, sharing, and meeting state.
- Recovery that reopens a closed Meet target and resumes the bounded admission flow.

Not yet complete and therefore not grounds for calling the product finished:

- Reliable named-speaker attribution when Meet caption metadata is unavailable.
- Rich-format editing beyond generated text/JSON/CSV, reports, and presentations.
- Model-directed native computer use beyond the controlled Chrome screen-share picker boundary.
- Three consecutive fresh-start real Meet rehearsals meeting every completion criterion.

---

## 2. Product Vision

Meetings frequently create work that must be completed after the conversation ends. Participants ask for data, charts, slides, summaries, research, code changes, and follow-up materials, but the meeting itself cannot produce those outputs.

Robin turns the meeting into an active work environment.

Instead of only documenting what was discussed, Robin can:

- Understand what participants need
- Ask clarifying questions
- Begin work while the meeting continues
- Incorporate follow-up instructions
- Produce useful artifacts
- Speak and present the result before the meeting ends

The long-term vision is an AI employee that can participate in meetings, operate a computer, use company tools, and complete knowledge-work tasks alongside human teammates.

---

## 3. Problem Statement

Current meeting assistants are primarily passive. They record, transcribe, summarize, and extract action items, but they generally do not complete the work being discussed.

Teams still need a person to leave the meeting, search for the correct information, use multiple applications, create an artifact, validate it, and bring it back to the group.

Robin addresses this gap by acting as a meeting participant capable of executing work in real time.

---

## 4. Goals

### 4.1 Hackathon MVP Goals

The MVP must demonstrate that Robin can:

1. Join a Google Meet as its own participant.
2. Continuously listen to the meeting.
3. Maintain shared conversational context.
4. Accept requests from any participant.
5. Recognize direct requests that mention “Robin.”
6. Recognize unmistakable implied requests and confirm when intent is uncertain.
7. Acknowledge accepted work aloud.
8. Select and read approved local workspace files through bounded tools.
9. Ground factual claims in sources Robin actually read.
10. Generate a short cited presentation and report from its analysis.
11. Continue listening while work is executing.
12. Accept follow-up instructions that modify active work.
13. Run a limited number of independent tasks concurrently.
14. Open completed work in a visible application or browser tab.
15. Share its screen or presentation tab in Google Meet.
16. Verbally explain the result.
17. Operate without human interaction after Robin has been launched on a pre-provisioned Mac.

### 4.2 Product Experience Goal

A meeting participant should feel as though they are delegating work to a capable junior coworker who is present in the meeting and using a separate computer.

---

## 5. Non-Goals

The hackathon MVP will not attempt to:

- Support every video-conferencing platform
- Provide production-grade enterprise security
- Operate on arbitrary personal computers without setup
- Handle unrestricted access to the entire filesystem
- Modify original source files by default
- Replace professional financial review or approval
- Guarantee perfect speaker identification
- Support unlimited concurrent tasks
- Automate every desktop application
- Use native PowerPoint, Keynote, or Excel GUI automation as the primary artifact-generation method
- Perform irreversible external actions such as sending emails, publishing documents, or merging code without explicit product expansion
- Provide multi-tenant cloud deployment
- Support meetings where Robin has not been invited or provided a valid meeting link

---

## 6. Target Users

### 6.1 Primary Users

Small teams conducting collaborative business meetings, including:

- Product teams
- Startup teams
- Operations teams
- Finance teams
- Consulting teams
- Internal strategy teams

### 6.2 Hackathon Users

- Judges participating in a live Google Meet
- Demo operators configuring Robin before the meeting
- Developers observing Robin through the local dashboard

---

## 7. Core User Stories

### Meeting Participation

- As a participant, I want Robin to join the meeting under its own identity so I can interact with it like another coworker.
- As a participant, I want Robin to understand the ongoing conversation so I do not need to restate all relevant context.
- As a participant, I want Robin to speak naturally so I know whether it accepted or completed a request.

### Task Delegation

- As any participant, I want to assign Robin a task by addressing it directly.
- As any participant, I want Robin to recognize an obvious request even when its name is not explicitly used.
- As any participant, I want Robin to ask a concise clarifying question when a request is ambiguous.
- As any participant, I want to modify or cancel a task while Robin is working.
- As any participant, I want Robin to manage more than one independent request without becoming unresponsive.

### Data and Artifact Creation

- As a participant, I want Robin to locate relevant files in an approved directory.
- As a participant, I want Robin to analyze spreadsheet data accurately.
- As a participant, I want Robin to use PDFs as supporting context.
- As a participant, I want Robin to generate a useful chart.
- As a participant, I want Robin to turn its analysis into a concise slide deck.
- As a participant, I want Robin to present the finished work during the same meeting.

### Operator Control

- As the operator, I want to paste a Google Meet link into a dashboard.
- As the operator, I want Robin to optionally join meetings from its calendar.
- As the operator, I want to see whether Robin is listening, working, speaking, or presenting.
- As the operator, I want an emergency stop control.
- As the operator, I want to inspect active tasks and generated outputs.

---

## 8. Primary Demo Scenario

### 8.1 Setup

The dedicated Mac is pre-provisioned with:

- Robin’s Google account
- Required macOS permissions
- A controlled Chrome profile
- Access to the approved local workspace
- Audio capture and virtual microphone routing
- Robin’s background service
- Robin’s local dashboard

The operator launches Robin and supplies a Google Meet link through the dashboard.

### 8.2 Demo Flow

1. Robin opens Google Meet and joins as “Robin — AI Coworker.”
2. Human participants begin discussing company performance.
3. A participant says:

   > “Robin, use the finance files to compare our 2024 quarterly results and make a few slides.”

4. Robin responds aloud:

   > “Got it. I’ll analyze the quarterly results and prepare a short deck.”

5. Robin searches its approved workspace.
6. Robin identifies the relevant CSV or Excel data.
7. Robin reads a PDF report for supporting context.
8. Robin generates a chart comparing quarterly performance.
9. Robin creates a three-to-five-slide presentation.
10. While Robin is working, another participant says:

    > “Robin, add operating margin and use actuals instead of forecasts.”

11. Robin recognizes this as a modification to the active task and responds:

    > “Understood. I’ll add operating margin and exclude forecasted values.”

12. Robin updates the analysis.
13. Robin validates the output against the source data.
14. Robin announces that the work is ready.
15. Robin opens the completed presentation in a dedicated browser tab.
16. Robin begins sharing the presentation tab in Google Meet.
17. Robin verbally explains the chart and key findings.
18. A participant requests a revision.
19. Robin updates the chart and refreshes the presentation.
20. Robin confirms completion and remains available for additional work.

### 8.3 Optional Secondary Demo

A second participant assigns an independent task while the first task is active. Robin executes both tasks concurrently within configured limits and reports progress separately.

---

## 9. Product Principles

### 9.1 Robin Is a Participant

Robin must appear and behave as an independent meeting participant, not as an invisible host-side assistant.

### 9.2 The Meeting Is the Primary Interface

Participants should interact with Robin through natural speech in the meeting. The local dashboard is for configuration, observability, and emergency control.

### 9.3 Hybrid Automation Over Pure GUI Automation

Robin should use programmatic tools for reliable analysis and artifact generation, then open and present the result through visible applications.

### 9.4 Shared Authority for the Demo

Every participant has equal authority to assign, modify, or cancel tasks.

### 9.5 Transparent Communication

Robin should acknowledge accepted tasks, ask clarifying questions when necessary, and announce when results are ready.

### 9.6 Bounded Autonomy

Robin may act autonomously within its approved workspace and supported task types. It should not perform irreversible or external actions outside that scope.

### 9.7 Accuracy Before Presentation

Robin must validate generated figures and references before presenting them as complete.

---

## 10. Functional Requirements

### 10.1 Meeting Discovery and Joining

#### Required

- Accept a Google Meet URL through the local dashboard.
- Support calendar-based meeting discovery as a secondary entry point.
- Join using Robin’s persistent Google account and Chrome profile.
- Handle the normal Google Meet pre-join flow.
- Join with the camera disabled by default.
- Join with the microphone muted until Robin needs to speak.
- Detect whether Robin successfully entered the meeting.
- Report meeting state through the dashboard.

#### Future

- Automatic calendar-based joining based on configurable rules
- Zoom and Microsoft Teams adapters
- Meeting invitation acceptance
- Waiting-room and admission-status handling across platforms

### 10.2 Meeting Listening

#### Required

- Capture meeting audio continuously.
- Stream audio for transcription.
- Maintain a timestamped rolling transcript.
- Preserve enough recent context to interpret follow-up requests.
- Detect when participants are speaking.
- Continue listening while tasks execute.
- Avoid transcribing Robin’s own synthesized voice as a new participant request where possible.

#### Desired

- Associate transcript turns with participant names.
- Use visual meeting information or captions to improve speaker attribution.
- Distinguish discussion from direct instructions.

### 10.3 Request Detection

Robin must classify conversation turns into at least:

- Non-task conversation
- Possible request
- Direct Robin request
- Confirmed task
- Clarification response
- Task modification
- Task cancellation
- Task status request

#### Behavior Rules

- Direct requests mentioning “Robin” should normally be accepted.
- Clear implied requests may be accepted when confidence is high.
- Ambiguous implied requests should trigger a short confirmation.
- Robin should not act on speculative discussion.
- Follow-up instructions should modify the relevant active task when context indicates continuity.
- Requests from all participants should be treated equally during the demo.

### 10.4 Spoken Interaction

#### Required

- Generate concise spoken acknowledgements.
- Unmute before speaking and mute afterward.
- Avoid speaking while another participant is actively talking unless interruption is necessary.
- Ask clarifying questions aloud.
- Announce when a task is ready.
- Explain completed work during presentation.
- Confirm accepted modifications and cancellations.

#### Tone

Robin should sound:

- Competent
- Concise
- Calm
- Collaborative
- Non-performative
- Similar to a professional coworker

### 10.5 Task Management

#### Required

- Create a structured task when a request is accepted.
- Record requester, timestamps, source context, requirements, and status.
- Support up to two independent tasks executing concurrently.
- Queue additional tasks when concurrency is exhausted.
- Merge follow-up requirements into an active task.
- Allow participants to cancel active or queued tasks.
- Report status when asked.
- Prevent duplicate execution of the same request.
- Preserve generated artifacts and task logs for the meeting session.

#### Task States

- Proposed
- Awaiting clarification
- Accepted
- Queued
- Acknowledging
- Executing
- Validating
- Ready to present
- Presenting
- Completed
- Failed
- Cancelled
- Awaiting confirmation
- Blocked
- Verified

Execution status and outcome state are persisted separately: for example, a task may remain ready
to present while its live presentation outcome is blocked by admission or a native dialog.

### 10.6 Workspace and File Access

#### Required

- Restrict Robin to one configured workspace directory.
- Treat source files as read-only.
- Save generated files to a separate output directory.
- Support:
  - CSV
  - XLSX
  - PDF
  - PPTX
  - Markdown and plain text
- Index available files before or during the meeting.
- Search files using filenames, metadata, extracted text, and semantic relevance.
- Record which files were used for each task.
- Handle missing, malformed, or irrelevant files gracefully.

#### Default Workspace Structure

```text
RobinWorkspace/
├── source-data/
├── generated/
├── sessions/
└── cache/
```

### 10.7 Data Analysis

#### Required

- Inspect CSV and Excel schemas.
- Select relevant sheets, columns, and ranges.
- Perform filtering, aggregation, comparisons, and calculations.
- Use PDF content as supporting narrative context.
- Generate chart-ready datasets.
- Preserve units and labels.
- Validate derived metrics against source values.
- Surface uncertainty when source data is ambiguous.

### 10.8 Chart Generation

#### Required

- Generate a chart appropriate to the request.
- Include a descriptive title.
- Label axes and units.
- Use legible formatting for screen sharing.
- Save the chart as a reusable artifact.
- Support at least:
  - Bar charts
  - Line charts
  - Percentage comparisons
  - Multi-series comparisons
- Regenerate charts when participants request revisions.

### 10.9 Slide Generation

#### Required

- Generate a three-to-five-slide deck.
- Include:
  - Title or request summary
  - Key chart
  - Supporting findings
  - Source or methodology note
- Use a consistent template.
- Keep content readable during screen sharing.
- Produce a browser-renderable presentation.
- Optionally export to PPTX or PDF.
- Refresh or regenerate slides after revisions.

### 10.10 Presentation

#### Required

- Open the generated presentation in a dedicated browser tab.
- Return focus to Google Meet.
- Initiate screen or tab sharing.
- Verify that presenting has begun.
- Navigate through slides.
- Speak over the presentation with a concise explanation.
- Stop presenting when complete or requested.
- Return to listening mode.

### 10.11 Local Dashboard

#### Required

The dashboard must display:

- Robin runtime status
- Meeting connection status
- Current meeting URL
- Audio capture health
- Virtual microphone health
- Browser automation health
- Live transcript
- Active tasks
- Queued tasks
- Task states
- Generated artifacts
- Error messages
- Emergency stop
- Incremental hearing/transcription activity
- Robin's durable beliefs and their sources
- Plans, tool actions, approval waits, verification, and recovery evidence
- Peak memory and workspace disk use against configured budgets

The dashboard must allow:

- Pasting a Meet URL
- Starting Robin
- Joining a meeting
- Leaving a meeting
- Selecting the approved workspace
- Enabling or disabling calendar auto-join
- Cancelling a task
- Stopping all Robin activity

### 10.12 Persistence

#### Required

Persist locally:

- Meeting sessions
- Transcript segments
- Task records
- Task modifications
- Files used
- Generated artifacts
- Errors and recovery attempts
- Runtime health events

---

## 11. Autonomy Rules

### Allowed Without Additional Approval

Robin may:

- Join a supplied or approved calendar meeting
- Listen to the meeting
- Speak in the meeting
- Read files from the approved workspace
- Generate new charts and slides
- Open local applications and browser tabs needed for supported tasks
- Share its generated presentation
- Modify generated artifacts
- Cancel its own work when instructed

### Not Allowed in the MVP

Robin may not:

- Modify original source files
- Access files outside the approved workspace
- Send external emails or messages
- Upload generated work to third-party services
- Publish or distribute artifacts outside the meeting
- Make purchases
- Merge code
- Delete user files
- Change account settings
- Invite additional participants

---

## 12. Success Metrics

### 12.1 Demo Success Criteria

The demo is successful when Robin can complete the full primary flow without human control after launch:

- Joins the correct Google Meet
- Is visible as an independent participant
- Hears and transcribes the request
- Accepts a task from any participant
- Acknowledges the task aloud
- Finds the correct local files
- Produces an accurate chart
- Produces a coherent slide deck
- Incorporates at least one spoken follow-up modification
- Shares the completed presentation
- Explains the result aloud
- Returns to listening mode

### 12.2 Target Performance

These are target goals rather than hard guarantees:

- Direct request detection accuracy: at least 90% in controlled demo conditions
- False task activation: no more than one per 30-minute controlled meeting
- Initial acknowledgement latency: under 4 seconds
- Simple chart completion: under 90 seconds
- Three-to-five-slide deck completion: under 3 minutes
- Follow-up modification recognition: under 5 seconds
- Meet-control success rate: at least 90% across repeated demo rehearsals
- Successful recovery from one expected UI failure without operator intervention

### 12.3 Qualitative Success

Judges should clearly understand that:

- Robin is present in the meeting.
- Robin is hearing the same conversation as everyone else.
- Anyone can delegate work to Robin.
- Robin is using its own computer.
- Robin can continue working while the meeting continues.
- Robin can return with useful, editable work during the same meeting.

---

## 13. Edge Cases

Robin should handle:

- The requested file does not exist.
- Multiple files appear equally relevant.
- Spreadsheet columns are ambiguous.
- The participant changes requirements mid-task.
- Two participants give conflicting instructions.
- A participant cancels a task.
- A participant asks for task status.
- The meeting becomes silent.
- Participants speak over each other.
- Robin’s acknowledgement is interrupted.
- Robin loses audio capture.
- Robin is removed from the meeting.
- Google Meet changes layout.
- Screen sharing fails.
- The generated presentation fails to render.
- A PDF has no extractable text.
- The chart contains unsupported or invalid data.
- A second task is assigned while two tasks are already running.
- Robin hears its own voice through the meeting audio.
- The meeting ends while work is still executing.

---

## 14. Failure Behavior

When Robin cannot complete a task, it should:

1. Stop retrying after a bounded number of attempts.
2. Preserve the current task state and logs.
3. Explain the blocker concisely in the meeting when possible.
4. Avoid presenting incomplete work as final.
5. Return to listening mode unless the meeting connection itself failed.

Example:

> “I found two spreadsheets with conflicting 2024 totals. Which one should I treat as final?”

For technical failures:

> “I completed the analysis, but screen sharing failed. I’m retrying once and have saved the deck locally.”

---

## 15. Privacy and Safety Requirements

### Required for the MVP

- Display a visible indicator in the dashboard whenever Robin is listening.
- Restrict file access to the approved workspace.
- Store meeting data locally by default.
- Avoid sending source files to external services unless necessary for the configured model workflow.
- Log every file Robin reads.
- Log every generated artifact.
- Provide an emergency stop.
- Stop audio capture when Robin leaves the meeting.
- Avoid hidden recording behavior.
- Clearly identify Robin as an AI participant through its meeting name.

### Future Enterprise Requirements

- Participant consent workflows
- Configurable retention
- Encryption at rest
- Enterprise identity and access management
- Role-based authority
- Audit logs
- Data-loss prevention
- Policy controls for external actions
- Organization-specific tool permissions

---

## 16. Dependencies and Assumptions

The MVP assumes:

- Robin runs on a dedicated, logged-in Mac.
- The machine is pre-provisioned before judging.
- Required macOS permissions have already been granted.
- Robin has a stable internet connection.
- Robin has its own Google account.
- The Google Meet link is valid.
- The meeting permits Robin to join.
- The workspace contains prepared CSV, Excel, and PDF files.
- Source files are sufficiently structured for analysis.
- A virtual microphone or equivalent audio-routing mechanism is available.
- Browser automation can control the configured Chrome instance.
- Generated artifacts can be rendered locally.
- The demo does not require production multi-user deployment.

---

## 17. Scope Prioritization

### P0: Required for Demo

- Dashboard launch
- Pasted Meet link
- Independent Google Meet participant
- Continuous audio capture
- Streaming transcription
- Direct “Robin” request detection
- Spoken acknowledgement
- Local CSV and Excel analysis
- Basic PDF context extraction
- Chart generation
- Slide generation
- Presentation tab
- Automated screen sharing
- Spoken result explanation
- Follow-up task modification
- Emergency stop

### P1: Strong Demo Enhancements

- Calendar auto-join
- Speaker attribution
- Implied request detection
- Two concurrent tasks
- Live dashboard transcript
- Task queue visibility
- Slide revision during the meeting
- Visual computer-use recovery
- PPTX export
- Status responses

### P2: Post-Hackathon

- Zoom support
- Microsoft Teams support
- Code changes and pull requests
- Slack and email actions
- Cloud deployment
- Role-based participant permissions
- Native macOS application
- Enterprise security controls
- Multiple Robin workers
- Long-term organizational memory

---

## 18. Milestone Acceptance Criteria

### Milestone 1: Meeting Presence

- Robin can launch Chrome.
- Robin can navigate to a supplied Meet URL.
- Robin can join under its own identity.
- Robin can mute, unmute, and leave.
- Dashboard reflects meeting state.

### Milestone 2: Listening and Speaking

- Robin receives continuous meeting audio.
- Robin produces a rolling transcript.
- Robin detects a direct request.
- Robin acknowledges through the Meet microphone.
- Robin avoids repeatedly transcribing its own voice as a request.

### Milestone 3: Task Execution

- Robin searches the approved workspace.
- Robin reads CSV, XLSX, and PDF files.
- Robin produces an accurate chart.
- Robin creates a short slide deck.
- Task status is visible in the dashboard.

### Milestone 4: Live Presentation

- Robin opens the generated presentation.
- Robin begins presenting in Meet.
- Robin navigates slides.
- Robin explains the result aloud.
- Robin stops presenting and resumes listening.

### Milestone 5: Agentic Interaction

- Robin accepts a follow-up modification while working.
- Robin updates the existing task rather than creating a duplicate.
- Robin handles two independent tasks concurrently.
- Robin recovers from at least one simulated browser failure.

---

## 19. Open Product Questions

These questions do not block the first PRD but should be resolved during technical design or implementation:

1. Which voice provider will be used for the MVP?
2. How accurately can Robin attribute requests to named speakers?
3. What exact confidence threshold should trigger implied-task acceptance?
4. How should Robin resolve conflicting instructions from equal-authority participants?
5. Should task cancellation require the original requester or remain open to anyone?
6. What exact chart and slide templates should the demo use?
7. Should Robin verbally narrate progress or only acknowledge and report completion?
8. How much transcript history should remain in the active context window?
9. Which calendar provider and authentication flow will be used?
10. What is the fallback when automated screen sharing is blocked?
11. Should PDF processing remain entirely local?
12. What should happen when the meeting ends before a task finishes?

---

## 20. Recommended Demo Script

### Participant A

> “Robin, use the finance folder to compare our quarterly revenue for 2024 and make a short deck.”

### Robin

> “Got it. I’ll compare the quarterly results and prepare a short presentation.”

### Participant B

> “Robin, include operating margin, and only use actual results.”

### Robin

> “Understood. I’ll add operating margin and exclude forecasts.”

### Robin, after completion

> “The deck is ready. Revenue increased through the year, with the strongest growth in Q4, while operating margin improved in the second half. I’ll share the analysis now.”

Robin begins presenting.

### Participant C

> “Can you change the chart to show quarter-over-quarter growth percentages?”

### Robin

> “Yes. I’ll update the chart.”

The presentation refreshes.

### Robin

> “The revised chart now shows quarter-over-quarter growth. Q4 had the largest increase.”

---

## 21. Product Definition of Done

Robin is complete only after all automated checks and three consecutive fresh-start real Google
Meet rehearsals pass. The rehearsals must use different tasks and each must prove, from another
participant's side, bidirectional audio, correct understanding, grounded and cited output, the
correct shared surface, audible narration, live Q&A or revision, graceful leave, restored audio and
browser state, and persisted audit evidence. Simulator state, a local success flag, a screenshot,
or loopback audio by itself is insufficient. Until that evidence exists, the product remains an
active implementation even when individual subsystems pass.
