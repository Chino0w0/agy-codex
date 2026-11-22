import {
  App,
  applyDocumentTheme,
  applyHostStyleVariables,
} from "@modelcontextprotocol/ext-apps";
import { z } from "zod";

interface CommandEvent {
  seq: number;
  kind: "command";
  item_id: string;
  command: string;
  display_command: string;
  status: string;
  exit_code: number | null;
  output: string;
}

interface FileChangeItem {
  path: string;
  kind: string;
}

interface FileChangeEvent {
  seq: number;
  kind: "file_change";
  item_id: string;
  status: string;
  changes: FileChangeItem[];
}

interface CommentaryEvent {
  seq: number;
  kind: "commentary";
  item_id: string;
  text: string;
}

interface ErrorEvent {
  seq: number;
  kind: "error" | "log";
  message?: string;
  text?: string;
}

type TimelineEvent =
  | CommandEvent
  | FileChangeEvent
  | CommentaryEvent
  | ErrorEvent
  | {
      seq: number;
      kind: string;
      [k: string]: unknown;
    };

interface UsageInfo {
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  reasoning_output_tokens: number;
}

interface HandoffResult {
  status?: string;
  summary?: string;
  changed_files?: string[];
  verification?: Array<{ command: string; passed: boolean; notes: string }>;
  risks?: string[];
  next_steps?: string[];
}

interface RunSnapshot {
  run_id: string;
  source?: "mcp" | "cli";
  status: "running" | "done" | "error" | "cancelled";
  cwd: string;
  mode: string;
  config_mode: string;
  model?: string | null;
  effort?: string | null;
  prompt_preview?: string;
  started_at?: number;
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

type SemanticKind = "read" | "list" | "search" | "exec";

interface ClassifiedCmd {
  semantic: SemanticKind;
  label: string;
  isProseLabel: boolean;
}

type TimelineBlock =
  | { type: "commentary"; text: string }
  | { type: "group"; id: string; events: TimelineEvent[] };

const AuxPaneResponseSchema = z.object({ isError: z.boolean().optional() }).passthrough();

const app = new App({ name: "Codex Worker", version: "0.2.0" }, {});

let viewMode: "run" | "panel" = "run";
let panelRuns: RunSnapshot[] = [];
let activeRunId: string | null = null;
let currentRun: RunSnapshot | null = null;
let timelineEntries: TimelineEvent[] = [];
let itemIndexById = new Map<string, number>();
let expandedCmdIds = new Set<string>();
let groupOpenOverrides = new Map<string, boolean>();
let traceExpanded = true;
let promptExpanded = false;
let latestSeq = 0;
let pollTimer: number | null = null;
let tickTimer: number | null = null;
let panelRefreshTimer: number | null = null;
let localStartedMs = Date.now();
let pollInFlight = false;
let userPinnedToBottom = true;
let savedScrollTop = 0;
let lastPanelSignature = "";

// Inline 14x14 monochrome SVG icons (Codex-style)
const ICONS = {
  chevron: `<svg class="chev" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.5L10.5 8 6 12.5"/></svg>`,
  chevronDown: `<svg class="chev open" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.5L10.5 8 6 12.5"/></svg>`,
  terminal: `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="1.75" y="2.75" width="12.5" height="10.5" rx="2"/><path d="M4.75 6.25L7 8 4.75 9.75"/><path d="M8.5 9.75h2.75"/></svg>`,
  folder: `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M2 4.25A1.25 1.25 0 0 1 3.25 3h3l1.3 1.5h5.2A1.25 1.25 0 0 1 14 5.75v6A1.25 1.25 0 0 1 12.75 13h-9.5A1.25 1.25 0 0 1 2 11.75v-7.5Z"/></svg>`,
  doc: `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 2.25h5l3 3v8.5A1.25 1.25 0 0 1 10.75 15h-6.5A1.25 1.25 0 0 1 3 13.75v-10.25A1.25 1.25 0 0 1 4.25 2.25Z"/><path d="M9 2.25v3h3"/><path d="M5.5 8.5h5M5.5 11h3.5"/></svg>`,
  search: `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="7" r="4.25"/><path d="M10.2 10.2L13.5 13.5"/></svg>`,
  fileEdit: `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 3.25l3.25 3.25-6.75 6.75H2.75V10l6.75-6.75Z"/></svg>`,
  arrowDown: `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3v9.5"/><path d="M4 8.75L8 12.75l4-4"/></svg>`,
};

function formatDuration(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m ${rem}s`;
}

function formatTokensCompact(usage?: UsageInfo | null, total?: number | null): string | null {
  if (usage) {
    const fmt = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
    return `${fmt(usage.input_tokens)} in · ${fmt(usage.cached_input_tokens)} cached · ${fmt(usage.output_tokens)} out`;
  }
  if (typeof total === "number") {
    return `${total.toLocaleString()} tok`;
  }
  return null;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatInlineMarkdown(text: string): string {
  const escaped = escapeHtml(text);
  return escaped
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
}

function panelSignature(runs: RunSnapshot[]): string {
  return runs.map((r) => `${r.run_id}:${r.status}:${r.source || "mcp"}`).join("|");
}

function toAbsFileUri(filePath: string, cwd: string): string {
  if (filePath.startsWith("file://")) return filePath;
  let abs = filePath;
  if (!filePath.startsWith("/") && !/^[A-Za-z]:[\\/]/.test(filePath)) {
    const base = cwd.replace(/[\\/]+$/, "");
    abs = `${base}/${filePath.replace(/^[\\/]+/, "")}`;
  }
  const normalized = abs.replace(/\\/g, "/");
  return normalized.startsWith("/") ? `file://${normalized}` : `file:///${normalized}`;
}

function shortFileName(filePath: string, cwd: string): string {
  const normFile = filePath.replace(/\\/g, "/");
  const normCwd = cwd.replace(/\\/g, "/").replace(/\/+$/, "");
  if (normCwd && normFile.startsWith(normCwd + "/")) {
    return normFile.slice(normCwd.length + 1);
  }
  return normFile;
}

function classifyCommand(cmdEv: CommandEvent): ClassifiedCmd {
  const raw = (cmdEv.display_command || cmdEv.command || "").trim();
  const failed = typeof cmdEv.exit_code === "number" && cmdEv.exit_code !== 0;
  if (failed) {
    return {
      semantic: "exec",
      label: `Command failed (${raw})`,
      isProseLabel: true,
    };
  }
  if (cmdEv.status === "in_progress" || cmdEv.exit_code === null) {
    return { semantic: "exec", label: raw, isProseLabel: false };
  }

  // Strip simple leading "cd ... && " or "pwd && " for classification
  const trimmed = raw.replace(/^(?:pwd|cd\s+\S+)\s*(?:&&|;)\s*/i, "").trim();

  // Read-like commands (cat, head, tail, sed -n) without pipes/redirects
  if (!/[|>;]/.test(trimmed)) {
    const readMatch = trimmed.match(/^(?:cat|head|tail|nl|bat)\s+(?:-\S+\s+)*(.+)$/);
    if (readMatch) {
      const target = readMatch[1].trim();
      const parts = target.split(/\s+/);
      if (parts.length <= 2) {
        return { semantic: "read", label: `已读取 ${target}`, isProseLabel: true };
      }
      return {
        semantic: "read",
        label: `已读取 ${parts[0]} 等 ${parts.length} 个文件`,
        isProseLabel: true,
      };
    }
    if (/^sed\s+-n\s+/.test(trimmed)) {
      const parts = trimmed.split(/\s+/);
      const last = parts[parts.length - 1] || "";
      if (last && !last.startsWith("-")) {
        return { semantic: "read", label: `已读取 ${last}`, isProseLabel: true };
      }
    }
    if (/^(?:rg\s+--files|find\s+|ls\b|tree\b)/.test(trimmed)) {
      return { semantic: "list", label: `已列出文件（${raw}）`, isProseLabel: true };
    }
    if (/^(?:rg|grep)\s+/.test(trimmed)) {
      return { semantic: "search", label: `已搜索（${raw}）`, isProseLabel: true };
    }
  }

  return { semantic: "exec", label: raw, isProseLabel: false };
}

function buildTimelineBlocks(events: TimelineEvent[]): TimelineBlock[] {
  const blocks: TimelineBlock[] = [];
  let currentGroup: TimelineEvent[] = [];
  let groupCounter = 0;

  const flushGroup = () => {
    if (currentGroup.length === 0) return;
    groupCounter += 1;
    blocks.push({
      type: "group",
      id: `grp_${groupCounter}`,
      events: currentGroup,
    });
    currentGroup = [];
  };

  for (const ev of events) {
    if (ev.kind === "commentary") {
      flushGroup();
      const text = String((ev as CommentaryEvent).text || "").trim();
      if (text) {
        blocks.push({ type: "commentary", text });
      }
    } else {
      currentGroup.push(ev);
    }
  }
  flushGroup();
  return blocks;
}

function summarizeGroup(events: TimelineEvent[]): { icon: string; title: string; hasRunning: boolean } {
  let reads = 0;
  let lists = 0;
  let searches = 0;
  let execs = 0;
  let fileEdits = 0;
  let failures = 0;
  let hasRunning = false;

  for (const ev of events) {
    if (ev.kind === "command") {
      const cmdEv = ev as CommandEvent;
      if (cmdEv.status === "in_progress" || cmdEv.exit_code === null) {
        hasRunning = true;
      } else if (typeof cmdEv.exit_code === "number" && cmdEv.exit_code !== 0) {
        failures += 1;
      }
      const cls = classifyCommand(cmdEv);
      if (cls.semantic === "read") reads += 1;
      else if (cls.semantic === "list") lists += 1;
      else if (cls.semantic === "search") searches += 1;
      else execs += 1;
    } else if (ev.kind === "file_change") {
      fileEdits += (ev as FileChangeEvent).changes?.length || 1;
    } else if (ev.kind === "error") {
      failures += 1;
    }
  }

  const exploreParts: string[] = [];
  if (reads > 0) exploreParts.push(`读取 ${reads} 个文件`);
  if (lists > 0) exploreParts.push(`列出目录 ${lists} 次`);
  if (searches > 0) exploreParts.push(`搜索 ${searches} 次`);

  const actionParts: string[] = [];
  if (exploreParts.length > 0) actionParts.push(exploreParts.join("、"));
  if (execs > 0) actionParts.push(`运行 ${execs} 条命令`);
  if (fileEdits > 0) actionParts.push(`变更 ${fileEdits} 个文件`);

  let icon = ICONS.terminal;
  if (fileEdits > 0 && execs === 0 && exploreParts.length === 0) {
    icon = ICONS.fileEdit;
  } else if (exploreParts.length > 0 && execs === 0 && fileEdits === 0) {
    icon = ICONS.folder;
  }

  let title = actionParts.join(" · ") || `${events.length} 项操作`;
  if (hasRunning) {
    title = `正在执行（${title}）`;
  } else if (exploreParts.length > 0 && execs === 0 && fileEdits === 0) {
    title = `已探索（${exploreParts.join(" · ")}）`;
  }
  if (failures > 0) {
    title += ` · ${failures} 项失败`;
  }

  return { icon, title, hasRunning };
}

function applyIncomingEvents(events: TimelineEvent[]): void {
  for (const ev of events) {
    if (ev.kind === "session" || ev.kind === "turn_done") {
      continue;
    }
    const itemId = typeof ev.item_id === "string" && ev.item_id ? ev.item_id : null;
    if (itemId && itemIndexById.has(itemId)) {
      const idx = itemIndexById.get(itemId)!;
      timelineEntries[idx] = ev;
      if (ev.kind === "command" && typeof ev.exit_code === "number" && ev.exit_code !== 0) {
        expandedCmdIds.add(itemId);
      }
    } else {
      const idx = timelineEntries.length;
      timelineEntries.push(ev);
      if (itemId) {
        itemIndexById.set(itemId, idx);
        if (ev.kind === "command" && typeof ev.exit_code === "number" && ev.exit_code !== 0) {
          expandedCmdIds.add(itemId);
        }
      }
    }
  }
}

function resetRunTimeline(run: RunSnapshot): void {
  currentRun = run;
  activeRunId = run.run_id;
  timelineEntries = [];
  itemIndexById = new Map();
  expandedCmdIds = new Set();
  groupOpenOverrides = new Map();
  traceExpanded = true;
  promptExpanded = false;
  latestSeq = 0;
  userPinnedToBottom = true;
  savedScrollTop = 0;
  localStartedMs = run.started_at ? run.started_at * 1000 : Date.now();
  if (Array.isArray(run.events)) {
    applyIncomingEvents(run.events);
    latestSeq = typeof run.latest_seq === "number" ? run.latest_seq : latestSeq;
  }
}

function stopPolling(): void {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
  if (tickTimer !== null) {
    window.clearInterval(tickTimer);
    tickTimer = null;
  }
}

function stopPanelRefresh(): void {
  if (panelRefreshTimer !== null) {
    window.clearInterval(panelRefreshTimer);
    panelRefreshTimer = null;
  }
}

function startPanelRefresh(): void {
  stopPanelRefresh();
  if (viewMode !== "panel") return;
  panelRefreshTimer = window.setInterval(() => {
    void refreshPanelList();
  }, 3000);
}

async function fetchIncrementalEvents(): Promise<void> {
  if (!activeRunId || pollInFlight) return;
  pollInFlight = true;
  try {
    const res = await app.callServerTool({
      name: "get_run_events",
      arguments: { run_id: activeRunId, since_seq: latestSeq },
    });
    const data = res?.structuredContent as RunSnapshot | undefined;
    if (!data) return;

    const prevStatus = currentRun?.status;
    const prevTokens = currentRun?.tokens_used ?? null;
    const prevError = currentRun?.error ?? null;
    const prevSeq = latestSeq;
    const prevResult = currentRun?.result ?? null;
    const incomingEvents = Array.isArray(data.events) ? data.events : [];

    currentRun = data;
    if (incomingEvents.length > 0) {
      applyIncomingEvents(incomingEvents);
    }
    if (typeof data.latest_seq === "number") {
      latestSeq = data.latest_seq;
    }

    const changed =
      incomingEvents.length > 0 ||
      data.status !== prevStatus ||
      (data.tokens_used ?? null) !== prevTokens ||
      (data.error ?? null) !== prevError ||
      latestSeq !== prevSeq ||
      JSON.stringify(data.result ?? null) !== JSON.stringify(prevResult);

    if (data.status !== "running") {
      stopPolling();
      if (viewMode === "panel" && prevStatus === "running") {
        void refreshPanelList();
      }
    }
    if (changed) {
      render();
    }
  } catch {
    // Ignore transient errors while view is closing or reloading
  } finally {
    pollInFlight = false;
  }
}

function startPolling(): void {
  stopPolling();
  if (!activeRunId || !currentRun || currentRun.status !== "running") {
    return;
  }
  pollTimer = window.setInterval(() => {
    void fetchIncrementalEvents();
  }, 350);
  tickTimer = window.setInterval(() => {
    const el = document.getElementById("elapsed-timer");
    if (el && currentRun?.status === "running") {
      const sec = Math.max(0, (Date.now() - localStartedMs) / 1000);
      el.textContent = `运行中 ${formatDuration(sec)}`;
    }
  }, 500);
  void fetchIncrementalEvents();
}

async function refreshPanelList(): Promise<void> {
  try {
    const res = await app.callServerTool({
      name: "list_runs",
      arguments: { limit: 20 },
    });
    const data = res?.structuredContent as { runs?: RunSnapshot[] } | undefined;
    if (Array.isArray(data?.runs)) {
      const sig = panelSignature(data.runs);
      if (sig === lastPanelSignature) {
        return;
      }
      lastPanelSignature = sig;
      panelRuns = data.runs;
      render();
    }
  } catch {
    // Ignore
  }
}

async function selectRun(runId: string): Promise<void> {
  activeRunId = runId;
  timelineEntries = [];
  itemIndexById = new Map();
  expandedCmdIds = new Set();
  groupOpenOverrides = new Map();
  latestSeq = 0;
  try {
    const res = await app.callServerTool({
      name: "get_run_events",
      arguments: { run_id: runId, since_seq: 0 },
    });
    const data = res?.structuredContent as RunSnapshot | undefined;
    if (data) {
      resetRunTimeline(data);
      render();
      if (data.status === "running") {
        startPolling();
      } else {
        stopPolling();
      }
    }
  } catch {
    render();
  }
}

async function openFileInAuxPane(filePath: string): Promise<void> {
  const cwd = currentRun?.cwd || ".";
  const uri = toAbsFileUri(filePath, cwd);
  try {
    await app.request(
      {
        method: "com.google.antigravity/toggle-aux-pane",
        params: { tabType: "fileView", uri },
      },
      AuxPaneResponseSchema,
    );
  } catch {
    // Gracefully ignore -32601 on surfaces without side-pane fileView
  }
}

function renderKindTag(kind: string): string {
  const lower = kind.toLowerCase();
  if (lower === "add" || lower === "create") return `<span class="kind-add">+</span>`;
  if (lower === "delete" || lower === "remove") return `<span class="kind-delete">-</span>`;
  return `<span class="kind-update">M</span>`;
}

function renderStepEvent(ev: TimelineEvent, idx: number, cwd: string): string {
  if (ev.kind === "command") {
    const cmdEv = ev as CommandEvent;
    const id = cmdEv.item_id || `cmd_${idx}`;
    const expanded = expandedCmdIds.has(id);
    const isRunning = cmdEv.status === "in_progress" || cmdEv.exit_code === null;
    const isFailed = typeof cmdEv.exit_code === "number" && cmdEv.exit_code !== 0;
    const cls = classifyCommand(cmdEv);

    const rowClass = `step-row${isFailed ? " is-failed" : ""}${isRunning ? " is-running" : ""}`;
    const leadIcon =
      cls.semantic === "read"
        ? ICONS.doc
        : cls.semantic === "list"
          ? ICONS.folder
          : cls.semantic === "search"
            ? ICONS.search
            : ICONS.terminal;
    const runningDot = isRunning
      ? `<span class="dot pulse" style="color:var(--color-text-info,#58a6ff)"></span>`
      : "";
    const labelClass = `step-label${cls.isProseLabel ? " is-prose-label" : ""}`;
    const outputBody = cmdEv.output
      ? `$ ${cmdEv.command}\n\n${cmdEv.output}`
      : `$ ${cmdEv.command}`;
    const outputHtml = expanded
      ? `<pre class="step-output">${escapeHtml(outputBody)}</pre>`
      : "";

    return `<div class="step-item">
      <button class="${rowClass}" data-toggle-cmd="${escapeHtml(id)}" title="${escapeHtml(cmdEv.command)}">
        ${leadIcon}${runningDot}<span class="${labelClass}">${escapeHtml(cls.label)}</span>
      </button>
      ${outputHtml}
    </div>`;
  }

  if (ev.kind === "file_change") {
    const fcEv = ev as FileChangeEvent;
    const chips = (fcEv.changes || [])
      .map((c) => {
        const name = shortFileName(c.path, cwd);
        return `<button class="file-link" data-open-file="${escapeHtml(c.path)}" title="打开 ${escapeHtml(c.path)}">${renderKindTag(c.kind)}<span>${escapeHtml(name)}</span></button>`;
      })
      .join("");
    return `<div class="file-row">${chips}</div>`;
  }

  if (ev.kind === "error" || ev.kind === "log") {
    const msg = String((ev as ErrorEvent).message || (ev as ErrorEvent).text || "");
    return `<div class="log-error">${escapeHtml(msg)}</div>`;
  }

  return "";
}

function renderHandoff(run: RunSnapshot): string {
  const res = run.result;
  if (!res || run.status === "running") return "";
  if (typeof res === "string") {
    return `<div class="handoff"><div class="handoff-summary">${formatInlineMarkdown(res)}</div></div>`;
  }

  const summaryHtml = res.summary
    ? `<div class="handoff-summary">${formatInlineMarkdown(res.summary)}</div>`
    : "";

  const verifyHtml =
    Array.isArray(res.verification) && res.verification.length > 0
      ? `<div class="verify-list">${res.verification
          .map((v) => {
            const cls = v.passed ? "exit-ok" : "exit-fail";
            const label = v.passed ? "PASS" : "FAIL";
            const notes = v.notes ? `<span class="verify-note">${escapeHtml(v.notes)}</span>` : "";
            return `<div class="verify-item"><span class="exit-badge ${cls}">${label}</span><span class="verify-cmd">${escapeHtml(v.command)}</span>${notes}</div>`;
          })
          .join("")}</div>`
      : "";

  const filesHtml =
    Array.isArray(res.changed_files) && res.changed_files.length > 0
      ? `<div class="file-row">${res.changed_files
          .map(
            (f) =>
              `<button class="file-link" data-open-file="${escapeHtml(f)}" title="在侧边栏打开">${escapeHtml(f)}</button>`,
          )
          .join("")}</div>`
      : "";

  const risksHtml =
    Array.isArray(res.risks) && res.risks.length > 0
      ? `<ul class="risks">${res.risks.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul>`
      : "";

  if (!summaryHtml && !verifyHtml && !filesHtml && !risksHtml) return "";
  return `<div class="handoff">${summaryHtml}${verifyHtml}${filesHtml}${risksHtml}</div>`;
}

function render(): void {
  const root = document.getElementById("root");
  if (!root) return;

  const existingBox = document.getElementById("timeline-box");
  if (existingBox) {
    savedScrollTop = existingBox.scrollTop;
    userPinnedToBottom =
      existingBox.scrollHeight - existingBox.scrollTop - existingBox.clientHeight < 36;
  }

  root.className = viewMode === "panel" ? "app is-panel" : "app is-inline";

  const panelBarHtml =
    viewMode === "panel"
      ? `<div class="panel-bar">
          <button class="icon-btn" id="refresh-runs-btn">刷新</button>
          ${
            panelRuns.length === 0
              ? `<span class="empty-hint" style="padding:2px 4px">暂无运行记录</span>`
              : panelRuns
                  .map((r) => {
                    const active = r.run_id === activeRunId ? " active" : "";
                    const dotCls = r.status === "running" ? "dot pulse" : "dot";
                    const colorStyle =
                      r.status === "running"
                        ? "color:var(--color-text-info,#58a6ff)"
                        : r.status === "done"
                          ? "color:var(--color-text-success,#3fb950)"
                          : "color:var(--color-text-danger,#f85149)";
                    const label = r.run_id.slice(9);
                    return `<button class="run-tab${active}" data-select-run="${escapeHtml(r.run_id)}"><span class="${dotCls}" style="${colorStyle}"></span>${escapeHtml(label)}</button>`;
                  })
                  .join("")
          }
        </div>`
      : "";

  if (!currentRun) {
    root.innerHTML = `${panelBarHtml}<div class="empty-hint">选择或启动一个 Codex Worker 任务以查看实时过程输出。</div>`;
    bindInteractions();
    return;
  }

  const run = currentRun;
  const elapsedSec =
    typeof run.duration_sec === "number"
      ? run.duration_sec
      : typeof run.elapsed_sec === "number"
        ? run.elapsed_sec
        : 0;
  const statusLabel =
    run.status === "running"
      ? `运行中 ${formatDuration(elapsedSec)}`
      : run.status === "done"
        ? `用时 ${formatDuration(elapsedSec)}`
        : run.status === "cancelled"
          ? `已取消 (${formatDuration(elapsedSec)})`
          : `执行失败 (${formatDuration(elapsedSec)})`;

  const statusDot =
    run.status === "running"
      ? `<span class="dot pulse" style="color:var(--color-text-info,#58a6ff)"></span>`
      : "";

  const metaParts: string[] = [];
  if (run.source === "cli") metaParts.push("CLI");
  if (run.model) metaParts.push(run.model);
  metaParts.push(`${run.mode} · ${run.config_mode}`);
  if (run.worktree?.branch) metaParts.push(run.worktree.branch);
  const tokenText = formatTokensCompact(run.usage, run.tokens_used);
  if (tokenText) metaParts.push(tokenText);

  const metaHtml = metaParts
    .map((p) => `<span>${escapeHtml(p)}</span>`)
    .join(`<span class="meta-sep">·</span>`);

  const stopBtn =
    run.status === "running"
      ? `<button class="stop-link" id="stop-run-btn">停止</button>`
      : "";

  const promptHtml = run.prompt_preview
    ? `<div class="prompt-strip${promptExpanded ? " expanded" : ""}" id="prompt-toggle" title="点击展开/收起任务指令">${promptExpanded ? ICONS.chevronDown : ICONS.chevron}<span class="prompt-strip-text">${escapeHtml(run.prompt_preview)}</span></div>`
    : "";

  const blocks = buildTimelineBlocks(timelineEntries);
  const lastGroupIdx = (() => {
    for (let i = blocks.length - 1; i >= 0; i--) {
      if (blocks[i].type === "group") return i;
    }
    return -1;
  })();

  const timelineHtml =
    blocks.length === 0
      ? `<div class="empty-hint">${run.status === "running" ? "正在等待 Codex 输出..." : "暂无中间过程记录。"}</div>`
      : blocks
          .map((blk, bIdx) => {
            if (blk.type === "commentary") {
              return `<div class="commentary">${formatInlineMarkdown(blk.text)}</div>`;
            }

            const { icon, title, hasRunning } = summarizeGroup(blk.events);
            const isLastGroup = bIdx === lastGroupIdx;
            const defaultOpen =
              hasRunning || isLastGroup || blk.events.length <= 4;
            const isOpen = groupOpenOverrides.has(blk.id)
              ? groupOpenOverrides.get(blk.id)!
              : defaultOpen;

            const innerHtml = isOpen
              ? `<div class="group-rail">${blk.events
                  .map((ev, eIdx) => renderStepEvent(ev, eIdx, run.cwd))
                  .join("")}</div>`
              : "";

            return `<div class="group">
              <button class="group-header" data-toggle-group="${escapeHtml(blk.id)}" data-current-open="${isOpen ? "1" : "0"}">
                ${icon}<span class="group-header-label">${escapeHtml(title)}</span>${isOpen ? ICONS.chevronDown : ICONS.chevron}
              </button>
              ${innerHtml}
            </div>`;
          })
          .join("");

  const traceBodyHtml = traceExpanded
    ? `<div class="timeline-wrap">
        <div class="timeline" id="timeline-box">${timelineHtml}</div>
        <button class="jump-circle-btn" id="jump-latest-btn" title="回到底部" style="display:${!userPinnedToBottom ? "inline-flex" : "none"}">${ICONS.arrowDown}</button>
      </div>`
    : "";

  root.innerHTML = `${panelBarHtml}<div class="shell">
    <div class="trace-header">
      <button class="trace-toggle${run.status === "error" ? " is-error" : ""}" id="trace-toggle-btn">
        ${statusDot}<span id="elapsed-timer">${escapeHtml(statusLabel)}</span>${traceExpanded ? ICONS.chevronDown : ICONS.chevron}
      </button>
      <div class="trace-meta">${metaHtml}${stopBtn}</div>
    </div>
    ${promptHtml}
    ${traceBodyHtml}
    ${renderHandoff(run)}
  </div>`;

  bindInteractions();
  const box = document.getElementById("timeline-box");
  if (box) {
    if (userPinnedToBottom && run.status === "running") {
      box.scrollTop = box.scrollHeight;
      savedScrollTop = box.scrollTop;
    } else {
      box.scrollTop = savedScrollTop;
    }
    updateJumpButtonVisibility(box);
  }
}

function updateJumpButtonVisibility(box: HTMLElement): void {
  const jumpBtnEl = document.getElementById("jump-latest-btn");
  if (!jumpBtnEl) return;
  const canScroll = box.scrollHeight - box.clientHeight > 24;
  const pinned = box.scrollHeight - box.scrollTop - box.clientHeight < 36;
  jumpBtnEl.style.display = canScroll && !pinned ? "inline-flex" : "none";
}

function bindInteractions(): void {
  const timelineBox = document.getElementById("timeline-box");
  timelineBox?.addEventListener("scroll", () => {
    savedScrollTop = timelineBox.scrollTop;
    userPinnedToBottom =
      timelineBox.scrollHeight - timelineBox.scrollTop - timelineBox.clientHeight < 36;
    updateJumpButtonVisibility(timelineBox);
  });

  document.getElementById("jump-latest-btn")?.addEventListener("click", () => {
    userPinnedToBottom = true;
    const box = document.getElementById("timeline-box");
    if (box) {
      box.scrollTop = box.scrollHeight;
      savedScrollTop = box.scrollTop;
      updateJumpButtonVisibility(box);
    }
  });

  document.getElementById("trace-toggle-btn")?.addEventListener("click", () => {
    traceExpanded = !traceExpanded;
    render();
  });

  document.getElementById("prompt-toggle")?.addEventListener("click", () => {
    promptExpanded = !promptExpanded;
    render();
  });

  document.querySelectorAll<HTMLElement>("[data-toggle-group]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-toggle-group");
      const curOpen = btn.getAttribute("data-current-open") === "1";
      if (!id) return;
      groupOpenOverrides.set(id, !curOpen);
      render();
    });
  });

  document.querySelectorAll<HTMLElement>("[data-toggle-cmd]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-toggle-cmd");
      if (!id) return;
      if (expandedCmdIds.has(id)) {
        expandedCmdIds.delete(id);
      } else {
        expandedCmdIds.add(id);
      }
      render();
    });
  });

  document.querySelectorAll<HTMLElement>("[data-open-file]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const path = btn.getAttribute("data-open-file");
      if (path) void openFileInAuxPane(path);
    });
  });

  document.querySelectorAll<HTMLElement>("[data-select-run]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-select-run");
      if (id) void selectRun(id);
    });
  });

  document.getElementById("refresh-runs-btn")?.addEventListener("click", () => {
    void refreshPanelList();
  });

  document.getElementById("stop-run-btn")?.addEventListener("click", async () => {
    if (!activeRunId) return;
    try {
      await app.callServerTool({
        name: "cancel_run",
        arguments: { run_id: activeRunId },
      });
      await fetchIncrementalEvents();
    } catch {
      // Ignore
    }
  });
}

// Register handlers BEFORE connecting so no initial notification is missed.
app.ontoolinput = ({ arguments: args }) => {
  const a = (args || {}) as Record<string, unknown>;
  if (typeof a.prompt === "string") {
    viewMode = "run";
    currentRun = {
      run_id: "pending",
      status: "running",
      cwd: String(a.cwd || "."),
      mode: String(a.mode || "write"),
      config_mode: String(a.config_mode || "isolated"),
      model: typeof a.model === "string" ? a.model : null,
      prompt_preview: a.prompt.slice(0, 180),
      elapsed_sec: 0,
    };
    localStartedMs = Date.now();
    render();
  }
};

app.ontoolresult = (result) => {
  const sc = (result?.structuredContent || {}) as Record<string, unknown>;
  if (sc.view === "panel") {
    viewMode = "panel";
    panelRuns = Array.isArray(sc.runs) ? (sc.runs as RunSnapshot[]) : [];
    lastPanelSignature = panelSignature(panelRuns);
    startPanelRefresh();
    const targetId =
      typeof sc.selected_run_id === "string" && sc.selected_run_id
        ? sc.selected_run_id
        : panelRuns[0]?.run_id || null;
    if (targetId) {
      void selectRun(targetId);
    } else {
      currentRun = null;
      render();
    }
    return;
  }

  if (typeof sc.run_id === "string") {
    viewMode = "run";
    stopPanelRefresh();
    resetRunTimeline(sc as unknown as RunSnapshot);
    render();
    startPolling();
  }
};

app.ontoolcancelled = () => {
  stopPolling();
  stopPanelRefresh();
};

app.onhostcontextchanged = (ctx) => {
  if (ctx.theme) applyDocumentTheme(ctx.theme);
  if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
};

async function init(): Promise<void> {
  await app.connect();
  const ctx = app.getHostContext();
  if (ctx?.theme) applyDocumentTheme(ctx.theme);
  if (ctx?.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
}

void init();
