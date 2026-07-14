# Robin Control Surface

## Overview

A restrained operational console for supervising one Mac-hosted coworker. The physical scene is a producer monitoring a live meeting in a moderately lit room: dense enough for rapid scanning, light enough to keep warnings and live state legible for hours.

## Color

Use OKLCH tokens only. The palette anchors on a dark olive status color without leaning into botanical styling.

```css
:root {
  --bg: oklch(1 0 0);
  --surface: oklch(0.965 0.004 120);
  --surface-strong: oklch(0.925 0.007 120);
  --ink: oklch(0.18 0.018 120);
  --muted: oklch(0.45 0.018 120);
  --primary: oklch(0.36 0.085 120);
  --primary-hover: oklch(0.31 0.08 120);
  --accent: oklch(0.59 0.16 42);
  --danger: oklch(0.54 0.20 25);
  --warning: oklch(0.72 0.15 82);
  --success: oklch(0.54 0.12 145);
  --focus: oklch(0.58 0.16 250);
}
```

## Typography

Use the native SF/system sans stack. Labels and controls use a compact fixed scale from 0.75rem to 1rem; page title is 1.5rem. Monospaced text is reserved for timestamps, identifiers, and diagnostics.

## Layout

Desktop uses a fixed top command bar and a two-column workspace: the meeting timeline and task state occupy the wider column; live health, approval queue, and controls occupy the narrower column. At narrow widths the right rail becomes a single vertical flow, while emergency controls remain sticky.

## Components

- Status strip: connection, meeting, audio, model, and desktop state with text plus icon.
- Meeting intake: Zoom link, optional briefing, and one primary Join action.
- Transcript: chronological speaker turns with timestamps and interruption markers.
- Task activity: current goal, bounded action steps, verification, and recovery state.
- Approval request: inline risk explanation, exact pending action, expiry, approve and deny.
- Emergency controls: stop, takeover, mute, share, leave; destructive-looking styling only for emergency stop.
- Diagnostics: terse checks with actionable remediation; no secret values.

## Interaction and Motion

State transitions use 150–200ms opacity/color changes. Emergency stop is immediate. Live updates announce through polite ARIA regions, while approval and failure states use assertive alerts. All motion is disabled under reduced-motion preference.
