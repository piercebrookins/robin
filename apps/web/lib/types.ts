export type HealthItem = {
  name: string;
  ok: boolean;
  detail: string;
  checked_at: string;
};

export type TranscriptSegment = {
  id: string;
  speaker_name: string | null;
  text: string;
  created_at: string;
};

export type RobinTask = {
  id: string;
  title: string;
  requester_name: string | null;
  status: string;
  revision: number;
  request_text: string;
  requested_outcome: string;
  constraints: string[];
  error: string | null;
};

export type FileIndexRecord = {
  id: string;
  relative_path: string;
  file_type: string;
  sha256: string;
  size_bytes: number;
  summary: string;
  columns: string[];
  created_at: string;
};

export type WorkspaceSnapshot = {
  root: string;
  source_dir: string;
  generated_dir: string;
  sessions_dir: string;
  file_count: number;
  files: FileIndexRecord[];
};

export type Artifact = {
  id: string;
  task_id: string;
  revision: number;
  type: "chart_json" | "chart_png" | "deck_json" | "deck_pptx" | "validation_json" | "report_markdown" | "agent_result_json";
  path: string;
  url: string | null;
};

export type ValidationCheck = {
  name: string;
  ok: boolean;
  detail: string;
  source: string | null;
  expected: unknown;
  actual: unknown;
};

export type ValidationReport = {
  task_id: string;
  ok: boolean;
  checks: ValidationCheck[];
  source_paths: string[];
  generated_at: string;
};

export type SpeechRecord = {
  id: string;
  text: string;
  mode: string;
  voice: string;
  model: string;
  format: string;
  path: string | null;
  byte_count: number;
  duration_seconds: number | null;
  playback_device: string | null;
  playback_route: string | null;
  started_at: string;
  completed_at: string | null;
  error: string | null;
};

export type RuntimeSnapshot = {
  runtime_state: string;
  meeting_state: string;
  meeting_url: string | null;
  meeting_id: string;
  listening: boolean;
  presenting: boolean;
  capture_loop_running: boolean;
  calendar_auto_join_running: boolean;
  health: HealthItem[];
  transcript: TranscriptSegment[];
  tasks: RobinTask[];
  artifacts: Artifact[];
  speech: SpeechRecord[];
  presentations: PresentationSession[];
};

export type EventEnvelope = {
  id: number | null;
  type: string;
  timestamp: string;
  meeting_id: string | null;
  task_id: string | null;
  component: string;
  payload: Record<string, unknown>;
};

export type RuntimeMetrics = {
  event_count: number;
  transcript_count: number;
  task_count: number;
  completed_task_count: number;
  failed_task_count: number;
  active_task_count: number;
  artifact_count: number;
  speech_count: number;
  presentation_count: number;
  audio_capture_event_count: number;
  direct_request_count: number;
};

export type PreflightSnapshot = {
  ok: boolean;
  checks: HealthItem[];
};

export type CalendarEvent = {
  id: string;
  title: string;
  start: string;
  end: string;
  meeting_url: string;
  source: string;
  conflicted: boolean;
};

export type CalendarSnapshot = {
  enabled: boolean;
  provider: string;
  auto_join: boolean;
  auto_join_running: boolean;
  events: CalendarEvent[];
  conflicts: string[][];
  error: string | null;
};

export type AudioCaptureResult = {
  ok: boolean;
  path: string;
  result: Record<string, unknown>;
  error: string | null;
};

export type PresentationSession = {
  task_id: string;
  active_slide: number;
  slide_count: number;
  active: boolean;
  updated_at: string;
};

export type ChartSpec = {
  id: string;
  title: string;
  subtitle: string | null;
  series: Array<{ name: string; x: string[]; y: number[] }>;
  source_note: string;
};

export type DeckSpec = {
  id: string;
  task_id: string;
  revision: number;
  title: string;
  slides: Array<{
    type: string;
    title: string;
    body: string[];
    chart_id: string | null;
    metrics: Record<string, string>;
  }>;
  sources: Array<{ label: string; path: string; note: string }>;
};
