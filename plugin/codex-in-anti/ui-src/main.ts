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

type TimelineEvent = CommandEvent | FileChangeEvent | CommentaryEvent | ErrorEvent | {
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

const AuxPaneResponseSchema = z.object({ isError: z.boolean().optional() }).passthrough();

const app = new App({ name: "Codex Worker", version: "0.2.0" }, {});

let viewMode: "run" | "panel" = "run";
let panelRuns: RunSnapshot[] = [];
let activeRunId: string | null = null;
let currentRun: RunSnapshot | null = null;
let timelineEntries: TimelineEvent[] = [];
let itemIndexById = new Map<string, number>();
let expandedCmdIds = new Set<string>();
let latestSeq = 0;
let pollTimer: number | null = null;
let tickTimer: number | null = null;
let panelRefreshTimer: number | null = null;
let localStartedMs = Date.now();
let pollInFlight = false;
let userPinnedToBottom = true;
let savedScrollTop = 0;
let lastPanelSignature = "";

function formatTokens(usage?: UsageInfo | null, total?: number | null): string | null {
  if (usage) {
    const fmt = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
    return `in ${fmt(usage.input_tokens)} · cached ${fmt(usage.cached_input_tokens)} · out ${fmt(usage.output_tokens)}`;
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
      el.textContent = `${sec.toFixed(1)}s`;
    }
  }, 150);
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

function renderStatusBadge(status: RunSnapshot["status"]): string {
  const labels: Record<RunSnapshot["status"], string> = {
    running: "Running",
    done: "Done",
    error: "Failed",
    cancelled: "Cancelled",
  };
  const pulse = status === "running" ? " pulse" : "";
  return `<span class="status-pill status-${status}"><span class="dot${pulse}"></span>${labels[status]}</span>`;
}

function renderKindTag(kind: string): string {
  const lower = kind.toLowerCase();
  if (lower === "add" || lower === "create") return `<span class="kind-add">+</span>`;
  if (lower === "delete" || lower === "remove") return `<span class="kind-delete">-</span>`;
  return `<span class="kind-update">M</span>`;
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
              `<button class="file-chip" data-open-file="${escapeHtml(f)}" title="Open in side pane">${escapeHtml(f)}</button>`,
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
          <button class="btn" id="refresh-runs-btn">Refresh</button>
          ${
            panelRuns.length === 0
              ? `<span class="empty-hint" style="padding:2px 4px">No Codex runs yet</span>`
              : panelRuns
                  .map((r) => {
                    const active = r.run_id === activeRunId ? " active" : "";
                    const dotCls = r.status === "running" ? "dot pulse" : "dot";
                    const colorStyle =
                      r.status === "running"
                        ? "color:var(--color-text-info,#0969da)"
                        : r.status === "done"
                          ? "color:var(--color-text-success,#1a7f37)"
                          : "color:var(--color-text-danger,#cf222e)";
                    const label = r.run_id.slice(9);
                    return `<button class="run-tab${active}" data-select-run="${escapeHtml(r.run_id)}"><span class="${dotCls}" style="${colorStyle}"></span>${escapeHtml(label)}</button>`;
                  })
                  .join("")
          }
        </div>`
      : "";

  if (!currentRun) {
    root.innerHTML = `${panelBarHtml}<div class="card"><div class="timeline"><div class="empty-hint">Select or start a Codex Worker task to view live execution progress.</div></div></div>`;
    bindInteractions();
    return;
  }

  const run = currentRun;
  const elapsedText =
    typeof run.duration_sec === "number"
      ? `${run.duration_sec.toFixed(2)}s`
      : typeof run.elapsed_sec === "number"
        ? `${run.elapsed_sec.toFixed(1)}s`
        : "0.0s";
  const tokenText = formatTokens(run.usage, run.tokens_used);
  const cmdCount = timelineEntries.filter((ev) => ev.kind === "command").length;
  const fileCount = timelineEntries.filter((ev) => ev.kind === "file_change").length;
  const stepSummary = [
    cmdCount > 0 ? `${cmdCount} cmd${cmdCount === 1 ? "" : "s"}` : "",
    fileCount > 0 ? `${fileCount} file${fileCount === 1 ? "" : "s"}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const stepPill = stepSummary ? `<span class="meta-pill">${escapeHtml(stepSummary)}</span>` : "";
  const worktreePill = run.worktree?.branch
    ? `<span class="meta-pill">${escapeHtml(run.worktree.branch)}</span>`
    : "";
  const sourcePill = run.source === "cli" ? `<span class="meta-pill">CLI</span>` : "";
  const modelPill = run.model ? `<span class="meta-pill">${escapeHtml(run.model)}</span>` : "";
  const modePill = `<span class="meta-pill">${escapeHtml(run.mode)} · ${escapeHtml(run.config_mode)}</span>`;
  const jumpBtn =
    run.status === "running"
      ? `<button class="btn jump-btn" id="jump-latest-btn" style="display:${userPinnedToBottom ? "none" : "inline-block"}">↓ Latest</button>`
      : "";
  const stopBtn =
    run.status === "running"
      ? `<button class="btn btn-danger" id="stop-run-btn">Stop</button>`
      : "";

  const promptHtml = run.prompt_preview
    ? `<div class="prompt-bar" title="${escapeHtml(run.prompt_preview)}">${escapeHtml(run.prompt_preview)}</div>`
    : "";

  const timelineHtml =
    timelineEntries.length === 0
      ? `<div class="empty-hint">${run.status === "running" ? "Waiting for Codex output..." : "No intermediate tool events recorded."}</div>`
      : timelineEntries
          .map((ev, idx) => {
            if (ev.kind === "commentary") {
              const txt = String(ev.text || "");
              return `<div class="commentary">${formatInlineMarkdown(txt)}</div>`;
            }
            if (ev.kind === "command") {
              const cmdEv = ev as CommandEvent;
              const id = cmdEv.item_id || `cmd_${idx}`;
              const expanded = expandedCmdIds.has(id);
              const badge =
                cmdEv.status === "in_progress" || cmdEv.exit_code === null
                  ? `<span class="exit-badge exit-run">running</span>`
                  : cmdEv.exit_code === 0
                    ? `<span class="exit-badge exit-ok">exit 0</span>`
                    : `<span class="exit-badge exit-fail">exit ${cmdEv.exit_code}</span>`;
              const outputBody = cmdEv.output
                ? `${cmdEv.command}\n\n${cmdEv.output}`
                : cmdEv.command;
              const outputHtml = expanded
                ? `<pre class="cmd-output">${escapeHtml(outputBody)}</pre>`
                : "";
              return `<div class="cmd-block">
                <button class="cmd-row" data-toggle-cmd="${escapeHtml(id)}">
                  <span class="cmd-left"><span>${expanded ? "▾" : "▸"}</span><span class="cmd-text">$ ${escapeHtml(cmdEv.display_command || cmdEv.command)}</span></span>
                  <span class="cmd-right">${badge}</span>
                </button>
                ${outputHtml}
              </div>`;
            }
            if (ev.kind === "file_change") {
              const fcEv = ev as FileChangeEvent;
              const chips = (fcEv.changes || [])
                .map((c) => {
                  const name = shortFileName(c.path, run.cwd);
                  return `<button class="file-chip" data-open-file="${escapeHtml(c.path)}" title="Open ${escapeHtml(c.path)}">${renderKindTag(c.kind)}<span>${escapeHtml(name)}</span></button>`;
                })
                .join("");
              return `<div class="file-row">${chips}</div>`;
            }
            if (ev.kind === "error" || ev.kind === "log") {
              const msg = String((ev as ErrorEvent).message || (ev as ErrorEvent).text || "");
              return `<div class="log-error">${escapeHtml(msg)}</div>`;
            }
            return "";
          })
          .join("");

  root.innerHTML = `${panelBarHtml}<div class="card">
    <div class="header">
      <div class="header-left">
        ${renderStatusBadge(run.status)}
        ${sourcePill}
        ${modelPill}
        ${modePill}
        ${worktreePill}
      </div>
      <div class="header-right">
        ${stepPill}
        ${tokenText ? `<span class="meta-pill">${escapeHtml(tokenText)}</span>` : ""}
        <span class="timer-pill" id="elapsed-timer">${elapsedText}</span>
        ${jumpBtn}
        ${stopBtn}
      </div>
    </div>
    ${promptHtml}
    <div class="timeline" id="timeline-box">${timelineHtml}</div>
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
  }
}

function bindInteractions(): void {
  const timelineBox = document.getElementById("timeline-box");
  timelineBox?.addEventListener("scroll", () => {
    savedScrollTop = timelineBox.scrollTop;
    userPinnedToBottom =
      timelineBox.scrollHeight - timelineBox.scrollTop - timelineBox.clientHeight < 36;
    const jumpBtnEl = document.getElementById("jump-latest-btn");
    if (jumpBtnEl) {
      jumpBtnEl.style.display =
        currentRun?.status === "running" && !userPinnedToBottom ? "inline-block" : "none";
    }
  });

  document.getElementById("jump-latest-btn")?.addEventListener("click", () => {
    userPinnedToBottom = true;
    const box = document.getElementById("timeline-box");
    if (box) {
      box.scrollTop = box.scrollHeight;
      savedScrollTop = box.scrollTop;
    }
    const jumpBtnEl = document.getElementById("jump-latest-btn");
    if (jumpBtnEl) {
      jumpBtnEl.style.display = "none";
    }
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
