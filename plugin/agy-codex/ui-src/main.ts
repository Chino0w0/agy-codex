import { App, applyDocumentTheme, applyHostStyleVariables } from "@modelcontextprotocol/ext-apps";
import { z } from "zod";
import type {
  CommandPreviewPayload,
  LocalSessionDetail,
  LocalSessionSummary,
  RunSnapshot,
  StatsPayload,
  TimelineEvent,
  WorkbenchConfig,
  WorkbenchPayload,
} from "./types";
import { escapeHtml, formatDuration } from "./utils";
import { renderRunTraceShell, type TraceViewState } from "./views/inline";
import { renderModelsTab, type ModelsViewState } from "./views/models";
import { renderStatsTab, type StatsViewState } from "./views/stats";
import { renderSubagentsTab, type SubagentFilter, type SubagentsViewState } from "./views/subagents";

const AuxPaneResponseSchema = z.object({ isError: z.boolean().optional() }).passthrough();

const app = new App({ name: "Agy Codex", version: "1.0.0" }, {});

let viewMode: "inline" | "panel" = "inline";
let activeTab: "subagents" | "stats" | "models" = "subagents";
let activeRunsCount = 0;
let maxConcurrentRuns = 5;

// Shared trace state for currently focused run (both inline and Subagents tab)
let activeRunId: string | null = null;
let currentRun: RunSnapshot | null = null;
let timelineEntries: TimelineEvent[] = [];
let itemIndexById = new Map<string, number>();
let expandedCmdIds = new Set<string>();
let groupOpenOverrides = new Map<string, boolean>();
let traceExpanded = true;
let promptExpanded = false;
let userPinnedToBottom = true;
let savedScrollTop = 0;
let latestSeq = 0;
let localStartedMs = Date.now();
let pollTimer: number | null = null;
let tickTimer: number | null = null;
let panelRefreshTimer: number | null = null;
let pollInFlight = false;

// Subagents tab state
let subFilter: SubagentFilter = "all";
let includeLocalSubagents = false;
let panelRuns: RunSnapshot[] = [];
let localSessions: LocalSessionSummary[] = [];
let selectedType: "run" | "local" = "run";
let selectedId: string | null = null;
let localDetail: LocalSessionDetail | null = null;
let actionFeedback: string | null = null;

// Stats tab state
let statsScope: "all" | "antigravity" | "codex_local" = "all";
let statsRange: "today" | "7d" | "30d" | "all" = "7d";
let statsPayload: StatsPayload | null = null;
let statsLoading = false;

// Models & Config tab state
let workbenchData: WorkbenchPayload | null = null;
let workbenchDraft: WorkbenchConfig | null = null;
let previewPayload: CommandPreviewPayload | null = null;
let modelSearchQuery = "";
let includeHiddenModels = false;
let savingConfig = false;
let configStatusMsg: string | null = null;
let configErrorMsg: string | null = null;

function canSendMessage(): boolean {
  const caps = app.getHostCapabilities() as Record<string, unknown> | undefined;
  return Boolean(caps && caps.message);
}

function canUpdateContext(): boolean {
  const caps = app.getHostCapabilities() as Record<string, unknown> | undefined;
  return Boolean(caps && caps.updateModelContext);
}

function cloneConfig(cfg: WorkbenchConfig): WorkbenchConfig {
  return JSON.parse(JSON.stringify(cfg)) as WorkbenchConfig;
}

function applyIncomingEvents(events: TimelineEvent[]): void {
  for (const ev of events) {
    if (ev.kind === "session" || ev.kind === "turn_done") continue;
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
    if (activeTab === "subagents") void refreshSubagentsData();
  }, 3500);
}

function startPolling(): void {
  stopPolling();
  if (!activeRunId || !currentRun || currentRun.status !== "running") return;
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
    const incomingEvents = Array.isArray(data.events) ? data.events : [];

    currentRun = data;
    if (incomingEvents.length > 0) applyIncomingEvents(incomingEvents);
    if (typeof data.latest_seq === "number") latestSeq = data.latest_seq;

    const changed =
      incomingEvents.length > 0 ||
      data.status !== prevStatus ||
      (data.tokens_used ?? null) !== prevTokens ||
      (data.error ?? null) !== prevError ||
      latestSeq !== prevSeq;

    if (data.status !== "running") {
      stopPolling();
      if (viewMode === "panel" && prevStatus === "running") {
        void refreshSubagentsData();
      }
    }
    if (changed) render();
  } catch {
    // Ignore transient reload errors
  } finally {
    pollInFlight = false;
  }
}

async function refreshSubagentsData(): Promise<void> {
  try {
    const [runsRes, localRes] = await Promise.all([
      app.callServerTool({ name: "list_runs", arguments: { limit: 40 } }),
      app.callServerTool({
        name: "list_local_sessions",
        arguments: { limit: 60, include_subagents: true },
      }),
    ]);
    const rData = runsRes?.structuredContent as
      | { runs?: RunSnapshot[]; active_runs?: number; max_concurrent_runs?: number }
      | undefined;
    if (Array.isArray(rData?.runs)) {
      panelRuns = rData!.runs;
      activeRunsCount = Number(rData?.active_runs ?? panelRuns.filter((r) => r.status === "running").length);
      if (typeof rData?.max_concurrent_runs === "number") {
        maxConcurrentRuns = rData.max_concurrent_runs;
      }
    }
    const lData = localRes?.structuredContent as { sessions?: LocalSessionSummary[] } | undefined;
    if (Array.isArray(lData?.sessions)) {
      localSessions = lData!.sessions;
    }

    if (!selectedId) {
      if (panelRuns.length > 0) {
        await selectRun(panelRuns[0].run_id);
        return;
      } else if (localSessions.length > 0) {
        await selectLocalSession(localSessions[0].thread_id);
        return;
      }
    }
    render();
  } catch {
    // Ignore
  }
}

async function selectRun(runId: string): Promise<void> {
  selectedType = "run";
  selectedId = runId;
  activeRunId = runId;
  actionFeedback = null;
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
      if (data.status === "running") startPolling();
      else stopPolling();
    }
  } catch {
    render();
  }
}

async function selectLocalSession(threadId: string): Promise<void> {
  selectedType = "local";
  selectedId = threadId;
  activeRunId = null;
  stopPolling();
  localDetail = null;
  actionFeedback = null;
  render();
  try {
    const res = await app.callServerTool({
      name: "get_local_session_detail",
      arguments: { thread_id: threadId },
    });
    const detail = res?.structuredContent as LocalSessionDetail | undefined;
    if (detail) {
      localDetail = detail;
      render();
    }
  } catch {
    render();
  }
}

async function refreshStats(): Promise<void> {
  statsLoading = true;
  render();
  try {
    const res = await app.callServerTool({
      name: "get_stats",
      arguments: { scope: statsScope, range: statsRange },
    });
    const data = res?.structuredContent as StatsPayload | undefined;
    if (data) statsPayload = data;
  } catch {
    // Ignore
  } finally {
    statsLoading = false;
    render();
  }
}

async function refreshWorkbenchConfig(keepDraft = false): Promise<void> {
  try {
    const res = await app.callServerTool({
      name: "get_workbench_config",
      arguments: { include_hidden: includeHiddenModels },
    });
    const data = res?.structuredContent as WorkbenchPayload | undefined;
    if (data) {
      workbenchData = data;
      maxConcurrentRuns = data.max_concurrent_runs || 5;
      if (!keepDraft || !workbenchDraft) {
        workbenchDraft = cloneConfig(data.workbench_config);
      }
      render();
    }
  } catch {
    // Ignore
  }
}

function syncDraftFromDom(): void {
  if (!workbenchDraft) return;
  const val = (id: string) => (document.getElementById(id) as HTMLInputElement | HTMLSelectElement | null)?.value ?? "";
  const checked = (id: string) => Boolean((document.getElementById(id) as HTMLInputElement | null)?.checked);

  if (document.getElementById("cfg-def-provider")) {
    workbenchDraft.defaults.provider = val("cfg-def-provider").trim() || null;
    workbenchDraft.defaults.model = val("cfg-def-model").trim() || null;
    workbenchDraft.defaults.effort = val("cfg-def-effort").trim() || null;
    const cm = val("cfg-def-config-mode");
    if (cm === "isolated" || cm === "profile" || cm === "inherit") {
      workbenchDraft.defaults.config_mode = cm;
    }

    const catMode = val("cfg-map-catalog-mode");
    if (catMode === "auto" || catMode === "custom" || catMode === "off") {
      workbenchDraft.mapping.catalog_mode = catMode;
    }
    workbenchDraft.mapping.catalog_path_override = val("cfg-map-catalog-path").trim() || null;
    const ep = val("cfg-map-effort-policy");
    if (ep === "clamp_with_warning" || ep === "passthrough") {
      workbenchDraft.mapping.effort_policy = ep;
    }
    const cwp = val("cfg-map-cw-policy");
    if (cwp === "same_model_only" || cwp === "always_forward" || cwp === "never_forward") {
      workbenchDraft.mapping.context_window_policy = cwp;
    }
    workbenchDraft.mapping.extra_whitelist_keys = val("cfg-map-extra-keys")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const mcpCheckboxes = document.querySelectorAll<HTMLInputElement>("[data-forward-mcp]");
    if (mcpCheckboxes.length > 0) {
      const fwdList: string[] = [];
      mcpCheckboxes.forEach((cb) => {
        const mid = cb.getAttribute("data-forward-mcp");
        if (cb.checked && mid && !fwdList.includes(mid)) {
          fwdList.push(mid);
        }
      });
      workbenchDraft.mapping.forwarded_mcp_servers = fwdList;
    }
    const mc = parseInt(val("cfg-map-max-conc"), 10);
    if (!Number.isNaN(mc) && mc >= 1 && mc <= 20) {
      workbenchDraft.mapping.max_concurrent_runs = mc;
    }
    workbenchDraft.mapping.allow_nested_subagents = checked("cfg-map-allow-nested");
  }

  document.querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-preset-field]").forEach((el) => {
    const pname = el.getAttribute("data-preset-name");
    const field = el.getAttribute("data-preset-field");
    if (!pname || !field || !workbenchDraft?.presets[pname]) return;
    const v = el.value.trim();
    if (field === "model") workbenchDraft.presets[pname].model = v || null;
    else if (field === "provider") workbenchDraft.presets[pname].provider = v || null;
    else if (field === "effort") workbenchDraft.presets[pname].effort = v || null;
    else if (field === "mode" && (v === "write" || v === "read-only")) {
      workbenchDraft.presets[pname].mode = v;
    }
  });
}

async function openFileInAuxPane(filePath: string): Promise<void> {
  const cwd = currentRun?.cwd || localDetail?.cwd || ".";
  const clean = filePath.replace(/^\\\\\?\\/, "");
  let abs = clean;
  if (!clean.startsWith("/") && !/^[A-Za-z]:[\\/]/.test(clean)) {
    const base = cwd.replace(/^\\\\\?\\/, "").replace(/[\\/]+$/, "");
    abs = `${base}/${clean.replace(/^[\\/]+/, "")}`;
  }
  const normalized = abs.replace(/\\/g, "/");
  const uri = normalized.startsWith("/") ? `file://${normalized}` : `file:///${normalized}`;
  try {
    await app.request(
      {
        method: "com.google.antigravity/toggle-aux-pane",
        params: { tabType: "fileView", uri },
      },
      AuxPaneResponseSchema,
    );
  } catch {
    // Ignore on global view or hosts without side pane
  }
}

function getTraceViewState(): TraceViewState {
  return {
    timelineEntries,
    expandedCmdIds,
    groupOpenOverrides,
    traceExpanded,
    promptExpanded,
    userPinnedToBottom,
  };
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

  if (viewMode === "inline") {
    if (!currentRun) {
      root.innerHTML = `<div class="empty-hint">等待 Codex Worker 启动...</div>`;
      bindInteractions();
      return;
    }
    root.innerHTML = renderRunTraceShell(currentRun, getTraceViewState());
    bindInteractions();
    restoreTimelineScroll();
    return;
  }

  const slotColor =
    activeRunsCount > 0 ? "var(--color-text-info,#58a6ff)" : "var(--color-text-success,#3fb950)";
  const topbarHtml = `<div class="topbar">
    <div class="nav-tabs">
      <button class="nav-tab${activeTab === "subagents" ? " active" : ""}" data-nav-tab="subagents">子 Agent 监控</button>
      <button class="nav-tab${activeTab === "stats" ? " active" : ""}" data-nav-tab="stats">用量统计</button>
      <button class="nav-tab${activeTab === "models" ? " active" : ""}" data-nav-tab="models">模型与配置</button>
    </div>
    <div class="topbar-right">
      <span class="slot-pill">
        <span class="dot${activeRunsCount > 0 ? " pulse" : ""}" style="color:${slotColor}"></span>
        活跃 ${activeRunsCount} / ${maxConcurrentRuns}
      </span>
      <button class="icon-btn" id="panel-refresh-btn">刷新</button>
    </div>
  </div>`;

  let bodyHtml = "";
  if (activeTab === "subagents") {
    const subState: SubagentsViewState = {
      filter: subFilter,
      includeLocalSubagents,
      runs: panelRuns,
      localSessions,
      selectedType,
      selectedId,
      currentRun,
      localDetail,
      traceState: getTraceViewState(),
      canSendMessage: canSendMessage(),
      canUpdateContext: canUpdateContext(),
      actionFeedback,
    };
    bodyHtml = renderSubagentsTab(subState);
  } else if (activeTab === "stats") {
    const stState: StatsViewState = {
      scope: statsScope,
      range: statsRange,
      stats: statsPayload,
      loading: statsLoading,
    };
    bodyHtml = renderStatsTab(stState);
  } else {
    const modState: ModelsViewState = {
      data: workbenchData,
      draft: workbenchDraft,
      preview: previewPayload,
      searchQuery: modelSearchQuery,
      includeHidden: includeHiddenModels,
      saving: savingConfig,
      statusMsg: configStatusMsg,
      errorMsg: configErrorMsg,
    };
    bodyHtml = renderModelsTab(modState);
  }

  root.innerHTML = `${topbarHtml}<div class="panel-body">${bodyHtml}</div>`;
  bindInteractions();
  restoreTimelineScroll();
}

function restoreTimelineScroll(): void {
  const box = document.getElementById("timeline-box");
  if (!box) return;
  if (userPinnedToBottom && currentRun?.status === "running") {
    box.scrollTop = box.scrollHeight;
    savedScrollTop = box.scrollTop;
  } else {
    box.scrollTop = savedScrollTop;
  }
  updateJumpButtonVisibility(box);
}

function updateJumpButtonVisibility(box: HTMLElement): void {
  const jumpBtnEl = document.getElementById("jump-latest-btn");
  if (!jumpBtnEl) return;
  const canScroll = box.scrollHeight - box.clientHeight > 24;
  const pinned = box.scrollHeight - box.scrollTop - box.clientHeight < 36;
  jumpBtnEl.style.display = canScroll && !pinned ? "inline-flex" : "none";
}

function bindInteractions(): void {
  // Navigation tabs
  document.querySelectorAll<HTMLElement>("[data-nav-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.getAttribute("data-nav-tab") as "subagents" | "stats" | "models" | null;
      if (!tab || tab === activeTab) return;
      activeTab = tab;
      if (tab === "subagents") void refreshSubagentsData();
      else if (tab === "stats") void refreshStats();
      else if (tab === "models") void refreshWorkbenchConfig();
      render();
    });
  });

  document.getElementById("panel-refresh-btn")?.addEventListener("click", () => {
    if (activeTab === "subagents") void refreshSubagentsData();
    else if (activeTab === "stats") void refreshStats();
    else if (activeTab === "models") void refreshWorkbenchConfig();
  });

  // Subagents filters & selection
  document.querySelectorAll<HTMLElement>("[data-sub-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      subFilter = (btn.getAttribute("data-sub-filter") as SubagentFilter) || "all";
      render();
    });
  });

  document.getElementById("toggle-local-subagents")?.addEventListener("change", (ev) => {
    includeLocalSubagents = Boolean((ev.target as HTMLInputElement).checked);
    render();
  });

  document.querySelectorAll<HTMLElement>("[data-select-run]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-select-run");
      if (id) void selectRun(id);
    });
  });

  document.querySelectorAll<HTMLElement>("[data-select-local]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-select-local");
      if (id) void selectLocalSession(id);
    });
  });

  // Timeline interactions
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
      if (expandedCmdIds.has(id)) expandedCmdIds.delete(id);
      else expandedCmdIds.add(id);
      render();
    });
  });

  document.querySelectorAll<HTMLElement>("[data-open-file]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const path = btn.getAttribute("data-open-file");
      if (path) void openFileInAuxPane(path);
    });
  });

  document.getElementById("stop-run-btn")?.addEventListener("click", async () => {
    if (!activeRunId) return;
    try {
      await app.callServerTool({ name: "cancel_run", arguments: { run_id: activeRunId } });
      await fetchIncrementalEvents();
    } catch {
      // Ignore
    }
  });

  document.getElementById("send-review-msg-btn")?.addEventListener("click", async () => {
    if (!currentRun) return;
    const resObj =
      currentRun.result && typeof currentRun.result === "object" ? currentRun.result : null;
    const files = resObj?.changed_files?.join(", ") || "无变更文件";
    const wt = currentRun.worktree?.branch ? `，分支 ${currentRun.worktree.branch}` : "";
    const msg = `请复核 Codex 子任务 ${currentRun.run_id}（模型 ${currentRun.model || "默认"}${wt}，改动: ${files}），检查 diff 并验证测试。`;
    try {
      await app.sendMessage({ role: "user", content: [{ type: "text", text: msg }] });
      actionFeedback = "已发送复核请求";
      render();
    } catch {
      actionFeedback = "当前视图不支持发送消息";
      render();
    }
  });

  document.getElementById("attach-context-btn")?.addEventListener("click", async () => {
    if (!currentRun) return;
    const resObj =
      currentRun.result && typeof currentRun.result === "object" ? currentRun.result : null;
    const files = resObj?.changed_files?.join(", ") || "无";
    const summary = resObj?.summary || currentRun.error || currentRun.prompt_preview || "";
    const resumeLine =
      currentRun.persist && currentRun.session_id
        ? `可续聊 session_id: ${currentRun.session_id}`
        : "临时会话（不可直接 resume，供后续新任务参考）";
    const ctxText = `[Agy Codex Run ${currentRun.run_id}] model=${currentRun.model || "默认"}, status=${currentRun.status}, ${resumeLine}\nchanged_files: ${files}\nsummary: ${summary}`;
    try {
      await app.updateModelContext({ content: [{ type: "text", text: ctxText }] });
      actionFeedback = "已附为下条消息上下文";
      render();
    } catch {
      actionFeedback = "当前视图不支持附加上下文";
      render();
    }
  });

  // Stats interactions
  document.querySelectorAll<HTMLElement>("[data-stats-scope]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const sc = btn.getAttribute("data-stats-scope") as "all" | "antigravity" | "codex_local" | null;
      if (sc) {
        statsScope = sc;
        void refreshStats();
      }
    });
  });

  document.querySelectorAll<HTMLElement>("[data-stats-range]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const rg = btn.getAttribute("data-stats-range") as "today" | "7d" | "30d" | "all" | null;
      if (rg) {
        statsRange = rg;
        void refreshStats();
      }
    });
  });

  // Copy buttons
  document.querySelectorAll<HTMLElement>("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const txt = btn.getAttribute("data-copy") || "";
      try {
        await navigator.clipboard.writeText(txt);
        const orig = btn.textContent;
        btn.textContent = "已复制";
        window.setTimeout(() => {
          btn.textContent = orig;
        }, 1200);
      } catch {
        // Ignore clipboard errors in restricted iframes
      }
    });
  });

  // Models & Config interactions
  document.getElementById("run-doctor-btn")?.addEventListener("click", async () => {
    configStatusMsg = null;
    configErrorMsg = null;
    await refreshWorkbenchConfig(true);
    configStatusMsg = "已完成环境体检";
    render();
  });

  document.getElementById("save-workbench-btn")?.addEventListener("click", async () => {
    syncDraftFromDom();
    if (!workbenchDraft) return;
    savingConfig = true;
    configStatusMsg = null;
    configErrorMsg = null;
    render();
    try {
      const res = await app.callServerTool({
        name: "save_workbench_config",
        arguments: workbenchDraft as unknown as Record<string, unknown>,
      });
      const data = res?.structuredContent as (WorkbenchPayload & { saved?: WorkbenchConfig }) | undefined;
      if (data) {
        workbenchData = data;
        if (data.saved) workbenchDraft = cloneConfig(data.saved);
        maxConcurrentRuns = data.max_concurrent_runs || 5;
        configStatusMsg = "配置已保存并立即生效";
      }
    } catch (err) {
      configErrorMsg = String(err);
    } finally {
      savingConfig = false;
      render();
    }
  });

  document.getElementById("preview-cmd-btn")?.addEventListener("click", async () => {
    syncDraftFromDom();
    try {
      const res = await app.callServerTool({
        name: "preview_resolved_command",
        arguments: {
          model: workbenchDraft?.defaults.model || undefined,
          provider: workbenchDraft?.defaults.provider || undefined,
          effort: workbenchDraft?.defaults.effort || undefined,
          config_mode: workbenchDraft?.defaults.config_mode || undefined,
        },
      });
      previewPayload = (res?.structuredContent as CommandPreviewPayload) || null;
      render();
    } catch (err) {
      configErrorMsg = String(err);
      render();
    }
  });

  document.getElementById("add-preset-btn")?.addEventListener("click", () => {
    syncDraftFromDom();
    if (!workbenchDraft) return;
    const baseName = `custom_${Object.keys(workbenchDraft.presets).length + 1}`;
    workbenchDraft.presets[baseName] = {
      label: baseName,
      model: null,
      provider: null,
      effort: "medium",
      mode: "write",
      when_to_use: "自定义任务预设",
    };
    render();
  });

  document.getElementById("add-custom-model-btn")?.addEventListener("click", () => {
    syncDraftFromDom();
    if (!workbenchDraft) return;
    const slug = (document.getElementById("new-cm-slug") as HTMLInputElement | null)?.value.trim() || "";
    const prov = (document.getElementById("new-cm-provider") as HTMLInputElement | null)?.value.trim() || null;
    const effRaw = (document.getElementById("new-cm-efforts") as HTMLInputElement | null)?.value.trim() || "";
    if (!slug) {
      configErrorMsg = "请输入自定义模型 slug";
      render();
      return;
    }
    const levels = effRaw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    workbenchDraft.custom_models = (workbenchDraft.custom_models || []).filter((m) => m.slug !== slug);
    workbenchDraft.custom_models.push({
      slug,
      display_name: slug,
      provider: prov,
      default_reasoning_level: levels[0] || null,
      supported_reasoning_levels: levels,
      context_window: null,
    });
    configStatusMsg = `已添加到草稿：${slug}（点击“保存配置”持久化）`;
    configErrorMsg = null;
    render();
  });

  document.querySelectorAll<HTMLElement>("[data-del-custom]").forEach((btn) => {
    btn.addEventListener("click", () => {
      syncDraftFromDom();
      const slug = btn.getAttribute("data-del-custom");
      if (!slug || !workbenchDraft) return;
      workbenchDraft.custom_models = workbenchDraft.custom_models.filter((m) => m.slug !== slug);
      render();
    });
  });

  document.querySelectorAll<HTMLElement>("[data-pick-default-model]").forEach((btn) => {
    btn.addEventListener("click", () => {
      syncDraftFromDom();
      const slug = btn.getAttribute("data-pick-default-model");
      if (!slug || !workbenchDraft) return;
      workbenchDraft.defaults.model = slug;
      configStatusMsg = `默认模型已选为 ${slug}（点击“保存配置”持久化）`;
      render();
    });
  });

  document.querySelectorAll<HTMLElement>("[data-toggle-exclude]").forEach((btn) => {
    btn.addEventListener("click", () => {
      syncDraftFromDom();
      const slug = btn.getAttribute("data-toggle-exclude");
      if (!slug || !workbenchDraft) return;
      const cur = new Set(workbenchDraft.excluded_models || []);
      if (cur.has(slug)) cur.delete(slug);
      else cur.add(slug);
      workbenchDraft.excluded_models = Array.from(cur);
      render();
    });
  });

  document.getElementById("toggle-hidden-models")?.addEventListener("change", (ev) => {
    syncDraftFromDom();
    includeHiddenModels = Boolean((ev.target as HTMLInputElement).checked);
    void refreshWorkbenchConfig(true);
  });

  document.getElementById("model-search-input")?.addEventListener("input", (ev) => {
    syncDraftFromDom();
    modelSearchQuery = (ev.target as HTMLInputElement).value;
    render();
    const el = document.getElementById("model-search-input") as HTMLInputElement | null;
    if (el) {
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    }
  });
}

// Register handlers BEFORE connect()
app.ontoolinput = ({ arguments: args }) => {
  const a = (args || {}) as Record<string, unknown>;
  if (typeof a.prompt === "string") {
    viewMode = "inline";
    currentRun = {
      run_id: "pending",
      status: "running",
      cwd: String(a.cwd || "."),
      mode: String(a.mode || "write"),
      config_mode: String(a.config_mode || "isolated"),
      model: typeof a.model === "string" ? a.model : null,
      provider: typeof a.provider === "string" ? a.provider : null,
      effort: typeof a.effort === "string" ? a.effort : null,
      preset: typeof a.preset === "string" ? a.preset : null,
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
    activeTab =
      sc.tab === "stats" || sc.tab === "models" || sc.tab === "subagents" ? sc.tab : "subagents";
    panelRuns = Array.isArray(sc.runs) ? (sc.runs as RunSnapshot[]) : [];
    activeRunsCount = Number(sc.active_runs ?? 0);
    maxConcurrentRuns = Number(sc.max_concurrent_runs ?? 5);
    startPanelRefresh();

    const targetId =
      typeof sc.selected_run_id === "string" && sc.selected_run_id
        ? sc.selected_run_id
        : panelRuns[0]?.run_id || null;

    if (activeTab === "stats") void refreshStats();
    else if (activeTab === "models") void refreshWorkbenchConfig();
    else {
      void refreshSubagentsData();
      if (targetId) void selectRun(targetId);
    }
    render();
    return;
  }

  if (typeof sc.run_id === "string") {
    viewMode = "inline";
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
