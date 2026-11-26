export interface CommandEvent {
  seq: number;
  kind: "command";
  item_id: string;
  command: string;
  display_command: string;
  status: string;
  exit_code: number | null;
  output: string;
}

export interface FileChangeItem {
  path: string;
  kind: string;
}

export interface FileChangeEvent {
  seq: number;
  kind: "file_change";
  item_id: string;
  status: string;
  changes: FileChangeItem[];
}

export interface CommentaryEvent {
  seq: number;
  kind: "commentary";
  item_id: string;
  text: string;
}

export interface ErrorEvent {
  seq: number;
  kind: "error" | "log";
  message?: string;
  text?: string;
}

export type TimelineEvent =
  | CommandEvent
  | FileChangeEvent
  | CommentaryEvent
  | ErrorEvent
  | {
      seq: number;
      kind: string;
      [k: string]: unknown;
    };

export interface UsageInfo {
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  reasoning_output_tokens: number;
}

export interface HandoffResult {
  status?: string;
  summary?: string;
  changed_files?: string[];
  verification?: Array<{ command: string; passed: boolean; notes: string }>;
  risks?: string[];
  next_steps?: string[];
}

export interface IdentityInfo {
  model?: string | null;
  provider?: string | null;
  effort?: string | null;
  original_effort?: string | null;
  preset?: string | null;
}

export interface RunSnapshot {
  run_id: string;
  source?: "mcp" | "cli";
  status: "running" | "done" | "error" | "cancelled";
  cwd: string;
  mode: string;
  config_mode: string;
  model?: string | null;
  provider?: string | null;
  effort?: string | null;
  preset?: string | null;
  persist?: boolean;
  requested?: IdentityInfo;
  effective?: IdentityInfo;
  observed?: { model?: string | null; effort?: string | null };
  warnings?: string[];
  prompt_preview?: string;
  started_at?: number;
  finished_at?: number | null;
  elapsed_sec?: number;
  duration_sec?: number | null;
  session_id?: string | null;
  tokens_used?: number | null;
  usage?: UsageInfo | null;
  worktree?: { path: string; branch: string; base_repo: string; cleanup_command: string } | null;
  result?: HandoffResult | string | null;
  error?: string | null;
  events?: TimelineEvent[];
  latest_seq?: number;
}

export interface LocalSessionSummary {
  thread_id: string;
  source: "codex_local";
  thread_kind: string;
  is_subagent: boolean;
  parent_thread_id?: string | null;
  child_threads?: Array<{ child_thread_id: string; status: string }>;
  model?: string | null;
  provider?: string | null;
  effort?: string | null;
  cwd: string;
  git_branch?: string;
  title?: string;
  originator?: string;
  created_at?: number;
  updated_at?: number;
  tokens_used?: number;
  usage?: UsageInfo | null;
  last_token_usage?: UsageInfo | null;
  model_context_window?: number | null;
}

export interface LocalSessionDetail extends LocalSessionSummary {
  context_percent?: number | null;
  context_raw_ratio?: number | null;
  child_details?: Array<{
    thread_id: string;
    status: string;
    title: string;
    model?: string | null;
    effort?: string | null;
    thread_kind?: string;
    tokens_used?: number;
  }>;
  snippets?: Array<{ role: string; text: string }>;
  warnings?: string[];
}

export interface StatsOverview {
  total_items: number;
  plugin_run_count: number;
  local_user_session_count: number;
  local_subagent_count: number;
  running_count: number;
  done_count: number;
  error_count: number;
  cancelled_count: number;
  success_rate: number | null;
  total_tokens: number;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  reasoning_output_tokens: number;
  cache_hit_rate: number | null;
  cache_coverage_count: number;
  avg_duration_sec: number | null;
  total_duration_sec: number;
}

export interface StatsDimRow {
  name: string;
  count: number;
  tokens: number;
  input_tokens: number;
  cached_tokens: number;
  output_tokens: number;
  cache_hit_rate: number | null;
  errors: number;
  avg_duration_sec: number | null;
}

export interface StatsPayload {
  scope: "all" | "antigravity" | "codex_local";
  range: "today" | "7d" | "30d" | "all";
  overview: StatsOverview;
  daily: Array<{
    date: string;
    count: number;
    plugin_count: number;
    local_count: number;
    tokens: number;
    cached_tokens: number;
    errors: number;
  }>;
  by_model: StatsDimRow[];
  by_provider: StatsDimRow[];
  by_effort: StatsDimRow[];
  recent_errors: Array<{ run_id: string; model: string; ts: number; error: string }>;
  warnings?: string[];
}

export interface CatalogModel {
  slug: string;
  display_name: string;
  visibility?: string;
  provider?: string | null;
  default_reasoning_level?: string | null;
  supported_reasoning_levels: string[];
  context_window?: number | null;
  source: string;
  excluded?: boolean;
}

export interface ProviderInfo {
  id: string;
  name: string;
  base_url?: string | null;
  wire_api?: string | null;
  requires_openai_auth?: boolean;
  env_key?: string | null;
  has_auth_token?: boolean;
  is_builtin?: boolean;
}

export interface PresetEntry {
  label: string;
  model?: string | null;
  provider?: string | null;
  effort?: string | null;
  mode: "write" | "read-only";
  when_to_use?: string;
}

export interface WorkbenchConfig {
  defaults: {
    model?: string | null;
    provider?: string | null;
    effort?: string | null;
    config_mode: "isolated" | "profile" | "inherit";
    mode: "write" | "read-only";
  };
  mapping: {
    catalog_mode: "auto" | "custom" | "off";
    catalog_path_override?: string | null;
    effort_policy: "clamp_with_warning" | "passthrough";
    context_window_policy: "same_model_only" | "always_forward" | "never_forward";
    extra_whitelist_keys: string[];
    allow_nested_subagents: boolean;
    max_concurrent_runs: number;
  };
  excluded_models: string[];
  custom_models: Array<{
    slug: string;
    display_name: string;
    provider?: string | null;
    default_reasoning_level?: string | null;
    supported_reasoning_levels: string[];
    context_window?: number | null;
  }>;
  presets: Record<string, PresetEntry>;
}

export interface DoctorCheckItem {
  ok: boolean;
  required: boolean;
  version?: string;
  path?: string | null;
  error?: string | null;
  hint?: string | null;
  [k: string]: unknown;
}

export interface DoctorReport {
  ok: boolean;
  recommended_config_mode: string;
  checks: Record<string, DoctorCheckItem>;
}

export interface WorkbenchPayload {
  models: CatalogModel[];
  all_models_count: number;
  visible_models_count: number;
  hidden_models_count: number;
  excluded_models: string[];
  providers: ProviderInfo[];
  presets: Record<string, PresetEntry>;
  max_concurrent_runs: number;
  catalog_mode: string;
  catalog_path?: string | null;
  catalog_loaded: boolean;
  models_cache_loaded: boolean;
  effective_defaults: Record<string, unknown>;
  workbench_config: WorkbenchConfig;
  standard_efforts: string[];
  warnings?: string[];
  doctor?: DoctorReport;
}

export interface CommandPreviewPayload {
  command: string[];
  command_line: string;
  effective: Record<string, unknown>;
  requested: Record<string, unknown>;
  catalog_path?: string | null;
  warnings: string[];
  redacted_overrides?: Record<string, unknown>;
}
