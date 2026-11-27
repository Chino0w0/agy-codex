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

export interface WorktreeInfo {
  path: string;
  branch: string;
  base_repo: string;
  cleanup_command: string;
}

export interface RunSnapshot {
  run_id: string;
  source?: "mcp" | "cli";
  origin?: "agent" | "user";
  raw?: boolean;
  status: "running" | "done" | "partial" | "error" | "cancelled" | "blocked";
  cwd: string;
  mode: string;
  config_mode: string;
  model?: string | null;
  provider?: string | null;
  effort?: string | null;
  preset?: string | null;
  persist?: boolean;
  pid?: number | null;
  requested?: IdentityInfo;
  effective?: IdentityInfo;
  observed?: { model?: string | null; effort?: string | null };
  warnings?: string[];
  prompt?: string | null;
  prompt_preview?: string;
  started_at?: number;
  updated_at?: number;
  finished_at?: number | null;
  elapsed_sec?: number;
  duration_sec?: number | null;
  timeout_sec?: number;
  remaining_timeout_sec?: number | null;
  completed_commands?: number;
  health?: "healthy" | "stalled" | "loop_warning" | "error";
  health_reason?: string | null;
  last_activity_at?: number | null;
  idle_sec?: number | null;
  in_flight_command?: string | null;
  recent_errors?: string[];
  session_id?: string | null;
  tokens_used?: number | null;
  usage?: UsageInfo | null;
  worktree?: WorktreeInfo | null;
  result?: HandoffResult | string | null;
  error?: string | null;
  events?: TimelineEvent[];
  latest_seq?: number;
  // v1.1 lifecycle & Antigravity linkage fields
  title?: string;
  role?: string;
  work_digest?: string;
  parent_session_id?: string | null;
  parent_session_title?: string | null;
  closed?: boolean;
  closed_at?: number | null;
  closed_by?: string | null;
  expired?: boolean;
  ttl_remaining_sec?: number | null;
}

export interface HistorySessionGroup {
  session_id: string;
  title: string;
  workspace?: string;
  updated_at?: number | string | null;
  active_count: number;
  closed_count: number;
  runs: RunSnapshot[];
}

export interface HistoryPayload {
  sessions: HistorySessionGroup[];
  total_runs: number;
  retention_hours?: number;
}

export interface ListRunsPayload {
  default_cwd?: string;
  runs: RunSnapshot[];
  scope_session_id?: string | null;
  scope_session_title?: string | null;
  scope_mode?: "session" | "all";
  total_unclosed_runs?: number;
  other_sessions_runs?: number;
  global_active_runs?: number;
  active_runs: number;
  closed_24h_count?: number;
  max_concurrent_runs: number;
  enabled?: boolean;
  intercept_native_subagents?: boolean;
  exclude_orchestrator_models?: boolean;
  retention_hours?: number;
}

export interface CloseRunPayload {
  closed_ids?: string[];
  runs?: RunSnapshot[];
  scope_session_id?: string | null;
  total_unclosed_runs?: number;
  other_sessions_runs?: number;
  global_active_runs?: number;
  active_runs?: number;
  closed_24h_count?: number;
}

export interface StatsOverview {
  total_items: number;
  plugin_run_count: number;
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
  cache_coverage_count?: number;
  avg_duration_sec: number | null;
  total_duration_sec: number;
  session_count?: number;
  archive_24h_count?: number;
  archive_storage_bytes?: number;
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
  done_count?: number;
  avg_duration_sec: number | null;
  success_rate?: number | null;
}

export interface StatsPayload {
  scope: "antigravity";
  range: "today" | "7d" | "30d" | "all";
  overview: StatsOverview;
  daily: Array<{
    date: string;
    count: number;
    plugin_count?: number;
    tokens: number;
    cached_tokens: number;
    errors: number;
  }>;
  by_model: StatsDimRow[];
  by_role?: StatsDimRow[];
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
  tool_mode?: string | null;
  supports_search_tool?: boolean | null;
  orchestrator_excluded?: boolean;
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

export interface McpServerInfo {
  id: string;
  type: string;
  command?: string | null;
  url?: string | null;
  enabled: boolean;
  forwarded: boolean;
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
  enabled?: boolean;
  intercept_native_subagents?: boolean;
  exclude_orchestrator_models?: boolean;
  retention_hours?: number;
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
    forwarded_mcp_servers: string[];
    native_passthrough_subagents?: string[];
    allow_nested_subagents: boolean;
    max_concurrent_runs: number;
    retention_hours?: number;
    worker_timeout_sec?: number;
    idle_timeout_sec?: number;
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
  default_cwd?: string;
  plugin_version?: string;
  models: CatalogModel[];
  all_models_count: number;
  visible_models_count: number;
  hidden_models_count: number;
  excluded_models: string[];
  providers: ProviderInfo[];
  mcp_servers?: McpServerInfo[];
  presets: Record<string, PresetEntry>;
  max_concurrent_runs: number;
  retention_hours?: number;
  enabled?: boolean;
  intercept_native_subagents?: boolean;
  exclude_orchestrator_models?: boolean;
  native_passthrough_subagents?: string[];
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

export interface ToggleExcludeOrchestratorPayload {
  enabled?: boolean;
  exclude_orchestrator_models?: boolean;
  workbench_config?: WorkbenchConfig;
}

export interface ToggleInterceptNativePayload {
  enabled?: boolean;
  intercept_native_subagents?: boolean;
  workbench_config?: WorkbenchConfig;
}

export type StartUserTaskPayload = RunSnapshot & {
  scope_session_id?: string | null;
  total_unclosed_runs?: number;
  other_sessions_runs?: number;
  global_active_runs?: number;
  active_runs: number;
  max_concurrent_runs: number;
  runs?: RunSnapshot[];
};

export interface UpdateCheckPayload {
  ok: boolean;
  repo: string;
  channel: "release" | "main";
  current_version: string;
  current_commit?: string | null;
  remote_version?: string | null;
  remote_commit?: string | null;
  remote_short_commit?: string | null;
  latest_id?: string | null;
  update_available: boolean;
  dismissed_version?: string | null;
  is_dismissed: boolean;
  release_url?: string | null;
  notes?: string | null;
  published_at?: string | null;
  checked_at?: number | null;
  from_cache: boolean;
  can_self_update: boolean;
  self_update_blocked_reason?: string | null;
  error?: string | null;
}

export interface UpdateStatusPayload {
  status: "idle" | "checking" | "downloading" | "verifying" | "installing" | "completed" | "failed";
  phase_label: string;
  current_version: string;
  current_commit?: string | null;
  target_version?: string | null;
  target_commit?: string | null;
  channel?: "release" | "main" | null;
  started_at?: number | null;
  updated_at?: number | null;
  completed_at?: number | null;
  restart_required: boolean;
  message?: string | null;
  error?: string | null;
  can_self_update: boolean;
  self_update_blocked_reason?: string | null;
  check?: UpdateCheckPayload | null;
}

export type ServerToolName =
  | "start_user_task"
  | "check_for_updates"
  | "start_plugin_update"
  | "get_update_status"
  | "get_run_events"
  | "list_runs"
  | "get_history"
  | "close_run"
  | "reopen_run"
  | "cancel_run"
  | "send_followup"
  | "get_stats"
  | "get_workbench_config"
  | "save_workbench_config"
  | "toggle_codex_enabled"
  | "toggle_exclude_orchestrator"
  | "toggle_intercept_native_subagents"
  | "run_doctor"
  | "preview_resolved_command";
