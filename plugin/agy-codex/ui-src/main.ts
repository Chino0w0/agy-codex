import { App, applyDocumentTheme, applyHostStyleVariables } from "@modelcontextprotocol/ext-apps";
import { z } from "zod";
import type {
  CloseRunPayload,
  CommandPreviewPayload,
  HistoryPayload,
  ListRunsPayload,
  RunSnapshot,
  StatsPayload,
  TimelineEvent,
  ToggleExcludeOrchestratorPayload,
  ToggleInterceptNativePayload,
  WorkbenchConfig,
  WorkbenchPayload,
} from "./types";
import { escapeHtml, formatDuration, getRunShortTitle, renderCodexToggleButton } from "./utils";
import { renderHistoryView, type HistoryFilter } from "./views/history";
import { renderModelsView } from "./views/models";
import { renderMonitorTablist, renderMonitorView } from "./views/monitor";
import { renderStatsView } from "./views/stats";
import { renderTraceView, type TraceViewState } from "./views/trace";

const AuxPaneResponseSchema = z.object({ isError: z.boolean().optional() }).passthrough();

const app = new App({ name: "Agy Codex", version: "1.2.0" }, {});

if (typeof HTMLInputElement !== "undefined" && !("switch" in HTMLInputElement.prototype)) {
  document.documentElement.classList.add("no-native-switch");
}

type PanelTab = "subagents" | "history" | "stats" | "models";

let viewMode: "inline" | "panel" = "inline";
let activeTab: PanelTab = "subagents";
let codexEnabled = true;
let interceptNativeSubagents = true;
let excludeOrchestratorModels = true;
let retentionHours = 24;
let activeRunsCount = 0;
let closed24hCount = 0;
let newArchiveBadge = false;
let maxConcurrentRuns = 5;

// Monitor / active trace state (strictly isolated from History!)
let panelRuns: RunSnapshot[] = [];
let activeRunId: string | null = null;
let currentRun: RunSnapshot | null = null;
let monitorTimeline: TimelineEvent[] = [];
let monitorItemIndexById = new Map<string, number>();
let monitorExpandedCmdIds = new Set<string>();
let monitorGroupOpenOverrides = new Map<string, boolean>();
let traceExpandedOverrides = new Map<string, boolean>();
let promptExpandedIds = new Set<string>();
let provenanceExpandedIds = new Set<string>();
let composerDrafts = new Map<string, string>();
let followupSending = false;
let monitorUserPinnedToBottom = true;
let monitorSavedScrollTop = 0;
let latestSeq = 0;
let localStartedMs = Date.now();
let localTimeoutDeadlineMs: number | null = null;
let actionFeedback: string | null = null;

// Timers & strict per-endpoint in-flight guards (zero polling when hidden!)
let pollTimer: number | null = null;
let tickTimer: number | null = null;
let panelRefreshTimer: number | null = null;
let pollInFlight = false;
let listInFlight = false;
let historyInFlight = false;
let statsInFlight = false;
let modelsInFlight = false;
let selectRunSeq = 0;
let historyDetailSeq = 0;
let historyPendingRefresh: { focusRunId?: string } | null = null;
let statsPendingReload = false;
let lastWakeupMs = 0;

// History tab state (strictly isolated from Monitor!)
let historyQuery = "";
let historyFilter: HistoryFilter = "all";
let historyPayload: HistoryPayload | null = null;
let historyLoading = false;
let historySelectedRunId: string | null = null;
let historySelectedRunDetail: RunSnapshot | null = null;
let historyInspecting = false;
let openSessionIds = new Set<string>();
let historySearchTimer: number | null = null;
let historyTimeline: TimelineEvent[] = [];
let historyExpandedCmdIds = new Set<string>();
let historyGroupOpenOverrides = new Map<string, boolean>();
let historySavedScrollTop = 0;

// Stats tab state
let statsRange: "today" | "7d" | "30d" | "all" = "7d";
let statsPayload: StatsPayload | null = null;
let statsLoading = false;
let statsLoadError: string | null = null;

// History error state
let historyLoadError: string | null = null;

// Models & Config tab state
let workbenchData: WorkbenchPayload | null = null;
let workbenchDraft: WorkbenchConfig | null = null;
let previewPayload: CommandPreviewPayload | null = null;
let modelSearchQuery = "";
let modelsLoading = false;
let modelsLoadError: string | null = null;
let codexToggleInFlight = false;
let savingConfig = false;
let configStatusMsg: string | null = null;
let configErrorMsg: string | null = null;

// Toast, Toggletip & Observer cleanup state
let toastTimer: number | null = null;
let popoverHintFallbackBound = false;
let interestPolyfillActiveTip: HTMLElement | null = null;
let interestCloseTimer: number | null = null;
let interestOpenTimer: number | null = null;
const tablistObservers = new Map<string, IntersectionObserver>();

function canSendMessage(): boolean {
  const caps = app.getHostCapabilities() as Record<string, unknown> | undefined;
  return Boolean(caps && caps.message);
}

function cloneConfig(cfg: WorkbenchConfig): WorkbenchConfig {
  return JSON.parse(JSON.stringify(cfg)) as WorkbenchConfig;
}

function showToast(msg: string, actionLabel?: string, actionCb?: () => void): void {
  const bar = document.getElementById("toast-bar");
  const msgEl = document.getElementById("toast-msg");
  const actEl = document.getElementById("toast-action") as HTMLButtonElement | null;
  if (!bar || !msgEl || !actEl) return;

  msgEl.textContent = msg;
  if (actionLabel && actionCb) {
    actEl.textContent = actionLabel;
    actEl.classList.remove("is-hidden");
    actEl.onclick = () => {
      bar.classList.add("is-hidden");
      actionCb();
    };
  } else {
    actEl.classList.add("is-hidden");
    actEl.onclick = null;
  }
  bar.classList.remove("is-hidden");
  if (toastTimer !== null) window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    bar.classList.add("is-hidden");
  }, 4200);
}

function updateLocalTiming(run: RunSnapshot): void {
  if (typeof run.started_at === "number" && run.started_at > 0) {
    localStartedMs = run.started_at * 1000;
  } else if (typeof run.elapsed_sec === "number" && run.elapsed_sec >= 0) {
    localStartedMs = Date.now() - run.elapsed_sec * 1000;
  } else {
    localStartedMs = Date.now();
  }
  if (run.status === "running") {
    if (typeof run.remaining_timeout_sec === "number") {
      localTimeoutDeadlineMs = Date.now() + Math.max(0, run.remaining_timeout_sec) * 1000;
    } else if (typeof run.timeout_sec === "number" && run.timeout_sec > 0) {
      localTimeoutDeadlineMs = localStartedMs + run.timeout_sec * 1000;
    } else {
      localTimeoutDeadlineMs = null;
    }
  } else {
    localTimeoutDeadlineMs = null;
  }
}

function resetMonitorTimelineState(): void {
  monitorTimeline = [];
  monitorItemIndexById = new Map();
  monitorExpandedCmdIds = new Set();
  monitorGroupOpenOverrides = new Map();
  latestSeq = 0;
  monitorUserPinnedToBottom = true;
  monitorSavedScrollTop = 0;
  localTimeoutDeadlineMs = null;
}

function applyIncomingEvents(events: TimelineEvent[]): void {
  for (const ev of events) {
    if (ev.kind === "session" || ev.kind === "turn_done") continue;
    const itemId = typeof ev.item_id === "string" && ev.item_id ? ev.item_id : null;
    if (itemId && monitorItemIndexById.has(itemId)) {
      const idx = monitorItemIndexById.get(itemId)!;
      monitorTimeline[idx] = ev;
      if (ev.kind === "command" && typeof ev.exit_code === "number" && ev.exit_code !== 0) {
        monitorExpandedCmdIds.add(itemId);
      }
    } else {
      const idx = monitorTimeline.length;
      monitorTimeline.push(ev);
      if (itemId) {
        monitorItemIndexById.set(itemId, idx);
        if (ev.kind === "command" && typeof ev.exit_code === "number" && ev.exit_code !== 0) {
          monitorExpandedCmdIds.add(itemId);
        }
      }
    }
  }
}

function resetRunTimeline(run: RunSnapshot): void {
  currentRun = run;
  activeRunId = run.run_id;
  resetMonitorTimelineState();
  updateLocalTiming(run);
  if (Array.isArray(run.events)) {
    applyIncomingEvents(run.events);
    latestSeq = typeof run.latest_seq === "number" ? run.latest_seq : latestSeq;
  }
}

function canPollActiveRun(): boolean {
  return (
    !document.hidden &&
    (viewMode === "inline" || (viewMode === "panel" && activeTab === "subagents")) &&
    Boolean(activeRunId) &&
    currentRun?.status === "running"
  );
}

function canPollMonitorList(): boolean {
  return !document.hidden && viewMode === "panel" && activeTab === "subagents";
}

function stopPolling(): void {
  if (pollTimer !== null) {
    window.clearTimeout(pollTimer);
    pollTimer = null;
  }
  if (tickTimer !== null) {
    window.clearInterval(tickTimer);
    tickTimer = null;
  }
}

function stopPanelRefresh(): void {
  if (panelRefreshTimer !== null) {
    window.clearTimeout(panelRefreshTimer);
    panelRefreshTimer = null;
  }
}

function syncTickTimer(): void {
  if (canPollActiveRun()) {
    if (tickTimer === null) {
      tickTimer = window.setInterval(() => {
        if (!currentRun || currentRun.status !== "running" || document.hidden) return;
        const sec = Math.max(0, (Date.now() - localStartedMs) / 1000);
        const durText = formatDuration(sec);
        const ctxTimer = document.getElementById("elapsed-timer");
        if (ctxTimer) ctxTimer.textContent = durText;
        const footerTimer = document.getElementById("running-footer-elapsed");
        if (footerTimer) footerTimer.textContent = `子 Agent 执行中 (${durText})`;
        const remEl = document.getElementById("timeout-remaining-hint");
        if (remEl && localTimeoutDeadlineMs !== null) {
          const remSec = Math.max(0, (localTimeoutDeadlineMs - Date.now()) / 1000);
          remEl.textContent = `超时剩余 ${formatDuration(remSec)}`;
          remEl.className = remSec < 180 ? "tabular-nums badge badge-warn" : "tabular-nums trace-muted";
        }
      }, 1000);
    }
  } else if (tickTimer !== null) {
    window.clearInterval(tickTimer);
    tickTimer = null;
  }
}

function scheduleNextEventPoll(): void {
  if (pollTimer !== null) {
    window.clearTimeout(pollTimer);
    pollTimer = null;
  }
  if (!canPollActiveRun() || pollInFlight) return;
  pollTimer = window.setTimeout(() => {
    pollTimer = null;
    void fetchIncrementalEvents();
  }, 1000);
}

function scheduleNextMonitorListRefresh(): void {
  if (panelRefreshTimer !== null) {
    window.clearTimeout(panelRefreshTimer);
    panelRefreshTimer = null;
  }
  if (!canPollMonitorList() || listInFlight) return;
  const delayMs = activeRunsCount > 0 ? 4500 : 8000;
  panelRefreshTimer = window.setTimeout(() => {
    panelRefreshTimer = null;
    void refreshMonitorRuns();
  }, delayMs);
}

function syncPollingTimers(): void {
  syncTickTimer();
  scheduleNextEventPoll();
  scheduleNextMonitorListRefresh();
}

function computeMonitorSnapshotSig(): string {
  const runsSig = panelRuns
    .map((r) => {
      const seqOrBucket =
        typeof r.latest_seq === "number"
          ? `s${r.latest_seq}`
          : `e${Math.floor((r.duration_sec ?? r.elapsed_sec ?? 0) / 5)}`;
      return `${r.run_id}:${r.status}:${r.closed ? 1 : 0}:${r.tokens_used ?? 0}:${seqOrBucket}`;
    })
    .join(",");
  return `${runsSig}|a=${activeRunsCount}|c=${closed24hCount}|m=${maxConcurrentRuns}|en=${codexEnabled ? 1 : 0}|eo=${excludeOrchestratorModels ? 1 : 0}|rh=${retentionHours}|act=${activeRunId ?? ""}`;
}

async function fetchIncrementalEvents(): Promise<void> {
  if (!activeRunId || pollInFlight || document.hidden) return;
  const targetRunId = activeRunId;
  pollInFlight = true;
  try {
    const res = await app.callServerTool({
      name: "get_run_events",
      arguments: { run_id: targetRunId, since_seq: latestSeq },
    });
    const data = res?.structuredContent as RunSnapshot | undefined;
    if (!data) return;

    const idx = panelRuns.findIndex((r) => r.run_id === data.run_id);
    if (idx >= 0) {
      panelRuns[idx] = { ...panelRuns[idx], ...data };
    }
    if (activeRunId !== targetRunId) return;

    const prevStatus = currentRun?.status;
    const prevTokens = currentRun?.tokens_used ?? null;
    const prevError = currentRun?.error ?? null;
    const prevSeq = latestSeq;
    const incomingEvents = Array.isArray(data.events) ? data.events : [];

    currentRun = data;
    updateLocalTiming(data);
    if (incomingEvents.length > 0) applyIncomingEvents(incomingEvents);
    if (typeof data.latest_seq === "number") latestSeq = data.latest_seq;

    const changed =
      incomingEvents.length > 0 ||
      data.status !== prevStatus ||
      (data.tokens_used ?? null) !== prevTokens ||
      (data.error ?? null) !== prevError ||
      latestSeq !== prevSeq;

    if (data.status !== "running" && viewMode === "panel" && prevStatus === "running") {
      void refreshMonitorRuns();
    }
    if (changed && (viewMode === "inline" || activeTab === "subagents")) {
      render();
    }
  } catch {
    // Ignore transient reload errors
  } finally {
    pollInFlight = false;
    syncTickTimer();
    scheduleNextEventPoll();
  }
}

function applyRunListMetadata(data: ListRunsPayload | CloseRunPayload): void {
  if (Array.isArray(data.runs)) {
    panelRuns = data.runs;
  }
  if (typeof data.active_runs === "number") {
    activeRunsCount = data.active_runs;
  } else if (Array.isArray(data.runs)) {
    activeRunsCount = data.runs.filter((r) => r.status === "running").length;
  }
  if (typeof data.closed_24h_count === "number") {
    closed24hCount = data.closed_24h_count;
  }
  if ("max_concurrent_runs" in data && typeof data.max_concurrent_runs === "number") {
    maxConcurrentRuns = data.max_concurrent_runs;
  }
  if ("enabled" in data && typeof data.enabled === "boolean") {
    codexEnabled = data.enabled;
  }
  if (
    "intercept_native_subagents" in data &&
    typeof data.intercept_native_subagents === "boolean"
  ) {
    interceptNativeSubagents = data.intercept_native_subagents;
  }
  if (
    "exclude_orchestrator_models" in data &&
    typeof data.exclude_orchestrator_models === "boolean"
  ) {
    excludeOrchestratorModels = data.exclude_orchestrator_models;
  }
  if ("retention_hours" in data && typeof data.retention_hours === "number") {
    retentionHours = data.retention_hours;
  }
}

async function refreshMonitorRuns(): Promise<void> {
  if (listInFlight) return;
  listInFlight = true;
  try {
    const prevSig = computeMonitorSnapshotSig();
    const prevActiveId = activeRunId;
    const res = await app.callServerTool({
      name: "list_runs",
      arguments: { status: "all", limit: 40, include_closed: false },
    });
    const data = res?.structuredContent as ListRunsPayload | undefined;
    if (!data) return;

    applyRunListMetadata(data);

    let focusedChanged = false;
    if (panelRuns.length === 0) {
      if (activeRunId !== null || currentRun !== null || monitorTimeline.length > 0) {
        focusedChanged = true;
      }
      activeRunId = null;
      currentRun = null;
      resetMonitorTimelineState();
    } else if (!activeRunId || !panelRuns.some((r) => r.run_id === activeRunId)) {
      if (viewMode === "panel" && activeTab === "subagents") {
        await selectRun(panelRuns[0].run_id);
        return;
      }
      activeRunId = panelRuns[0].run_id;
      currentRun = panelRuns[0];
      focusedChanged = true;
    } else if (currentRun && currentRun.run_id === activeRunId) {
      const fresh = panelRuns.find((r) => r.run_id === activeRunId);
      if (
        fresh &&
        fresh.status !== currentRun.status &&
        viewMode === "panel" &&
        activeTab === "subagents"
      ) {
        await selectRun(activeRunId);
        return;
      }
    }

    const nextSig = computeMonitorSnapshotSig();
    const sigChanged = nextSig !== prevSig || activeRunId !== prevActiveId || focusedChanged;
    if (sigChanged && viewMode === "panel" && activeTab === "subagents") {
      render();
    }
  } catch {
    // Ignore
  } finally {
    listInFlight = false;
    syncTickTimer();
    scheduleNextEventPoll();
    scheduleNextMonitorListRefresh();
  }
}

async function selectRun(runId: string): Promise<void> {
  const token = ++selectRunSeq;
  activeRunId = runId;
  actionFeedback = null;
  resetMonitorTimelineState();
  const listRun = panelRuns.find((r) => r.run_id === runId) || null;
  currentRun = listRun;
  if (listRun) updateLocalTiming(listRun);
  try {
    const res = await app.callServerTool({
      name: "get_run_events",
      arguments: { run_id: runId, since_seq: 0 },
    });
    if (token !== selectRunSeq || activeRunId !== runId) return;
    const data = res?.structuredContent as RunSnapshot | undefined;
    if (data) {
      resetRunTimeline(data);
      const idx = panelRuns.findIndex((r) => r.run_id === data.run_id);
      if (idx >= 0) {
        panelRuns[idx] = { ...panelRuns[idx], ...data };
      }
    }
  } catch {
    if (token !== selectRunSeq || activeRunId !== runId) return;
    currentRun = panelRuns.find((r) => r.run_id === runId) || null;
  }
  syncPollingTimers();
  if (viewMode === "inline" || activeTab === "subagents") {
    render();
  }
}

async function handleCloseRun(runId: string): Promise<void> {
  const closingRun = panelRuns.find((r) => r.run_id === runId);
  try {
    const res = await app.callServerTool({
      name: "close_run",
      arguments: { run_id: runId },
    });
    const data = res?.structuredContent as CloseRunPayload | undefined;
    if (data) {
      applyRunListMetadata(data);
    } else {
      panelRuns = panelRuns.filter((r) => r.run_id !== runId);
      activeRunsCount = panelRuns.filter((r) => r.status === "running").length;
      closed24hCount += 1;
    }
    newArchiveBadge = true;
    if (activeRunId === runId) {
      if (panelRuns.length > 0) {
        await selectRun(panelRuns[0].run_id);
      } else {
        activeRunId = null;
        currentRun = null;
        resetMonitorTimelineState();
        syncPollingTimers();
        render();
      }
    } else {
      syncPollingTimers();
      render();
    }
    const label = closingRun ? getRunShortTitle(closingRun) : runId;
    showToast(`已归档「${label}」(留存 ${retentionHours}h)`, "去历史对话", () => {
      switchTab("history", runId);
    });
  } catch (err) {
    showToast(`归档失败: ${String(err)}`);
  }
}

async function handleCloseAllFinished(): Promise<void> {
  const finishedCount = panelRuns.filter((r) => r.status !== "running").length;
  if (finishedCount === 0) {
    showToast("当前没有已完成的子 Agent 需要归档");
    return;
  }
  try {
    const res = await app.callServerTool({
      name: "close_run",
      arguments: { close_all_finished: true },
    });
    const data = res?.structuredContent as CloseRunPayload | undefined;
    if (data) {
      applyRunListMetadata(data);
    } else {
      panelRuns = panelRuns.filter((r) => r.status === "running");
      activeRunsCount = panelRuns.length;
    }
    newArchiveBadge = true;
    const closedCount = data?.closed_ids?.length ?? finishedCount;
    if (!panelRuns.some((r) => r.run_id === activeRunId)) {
      if (panelRuns.length > 0) {
        await selectRun(panelRuns[0].run_id);
      } else {
        activeRunId = null;
        currentRun = null;
        resetMonitorTimelineState();
        syncPollingTimers();
        render();
      }
    } else {
      syncPollingTimers();
      render();
    }
    showToast(`已归档 ${closedCount} 个已完成任务至「历史对话」`, "去历史对话", () => {
      switchTab("history");
    });
  } catch (err) {
    showToast(`批量归档失败: ${String(err)}`);
  }
}

async function handleReopenRun(runId: string): Promise<void> {
  try {
    const res = await app.callServerTool({
      name: "reopen_run",
      arguments: { run_id: runId },
    });
    const run = res?.structuredContent as RunSnapshot | undefined;
    activeTab = "subagents";
    if (run?.run_id) {
      panelRuns = [run, ...panelRuns.filter((r) => r.run_id !== run.run_id)];
      activeRunsCount = panelRuns.filter((r) => r.status === "running").length;
      await selectRun(run.run_id);
      showToast(`已将「${getRunShortTitle(run)}」恢复到活跃监控标签栏`);
    } else {
      await refreshMonitorRuns();
      await selectRun(runId);
    }
  } catch (err) {
    showToast(`恢复失败: ${String(err)}`);
  }
}

async function handleToggleCodexEnabled(nextVal: boolean): Promise<void> {
  if (codexToggleInFlight) return;
  codexToggleInFlight = true;
  if (activeTab === "models") syncDraftFromDom();
  const prevVal = codexEnabled;
  codexEnabled = nextVal;
  if (workbenchDraft) {
    workbenchDraft.enabled = nextVal;
  }
  render();
  try {
    const res = await app.callServerTool({
      name: "toggle_codex_enabled",
      arguments: { enabled: nextVal },
    });
    const data = res?.structuredContent as
      | { enabled?: boolean; workbench_config?: WorkbenchConfig }
      | undefined;
    if (data && typeof data.enabled === "boolean") {
      codexEnabled = data.enabled;
    }
    if (workbenchDraft) {
      workbenchDraft.enabled = codexEnabled;
    }
    render();
    showToast(
      codexEnabled
        ? "已开启 Codex 子代理联动，通用子任务将默认走 Codex"
        : "已暂停 Codex 子代理联动，新子任务已全部交由 Antigravity 原生子代理执行",
    );
  } catch (err) {
    codexEnabled = prevVal;
    if (workbenchDraft) {
      workbenchDraft.enabled = prevVal;
    }
    render();
    showToast(`切换失败: ${String(err)}`);
  } finally {
    codexToggleInFlight = false;
  }
}

async function handleToggleExcludeOrchestrator(nextVal: boolean): Promise<void> {
  if (activeTab === "models") syncDraftFromDom();
  const prevVal = excludeOrchestratorModels;
  excludeOrchestratorModels = nextVal;
  if (workbenchDraft) {
    workbenchDraft.exclude_orchestrator_models = nextVal;
  }
  render();
  try {
    const res = await app.callServerTool({
      name: "toggle_exclude_orchestrator",
      arguments: { enabled: nextVal },
    });
    const data = res?.structuredContent as ToggleExcludeOrchestratorPayload | undefined;
    if (data) {
      if (typeof data.exclude_orchestrator_models === "boolean") {
        excludeOrchestratorModels = data.exclude_orchestrator_models;
      } else if (typeof data.enabled === "boolean") {
        excludeOrchestratorModels = data.enabled;
      }
      if (workbenchDraft) {
        workbenchDraft.exclude_orchestrator_models = excludeOrchestratorModels;
      }
    }
    render();
    showToast(
      excludeOrchestratorModels
        ? "已开启 Orchestrator 模型排除保护"
        : "已解除 Orchestrator 模型派发限制",
    );
  } catch (err) {
    excludeOrchestratorModels = prevVal;
    if (workbenchDraft) {
      workbenchDraft.exclude_orchestrator_models = prevVal;
    }
    render();
    showToast(`切换保护开关失败: ${String(err)}`);
  }
}

async function handleToggleInterceptNative(nextVal: boolean): Promise<void> {
  if (activeTab === "models") syncDraftFromDom();
  const prevVal = interceptNativeSubagents;
  interceptNativeSubagents = nextVal;
  if (workbenchDraft) {
    workbenchDraft.intercept_native_subagents = nextVal;
  }
  render();
  try {
    const res = await app.callServerTool({
      name: "toggle_intercept_native_subagents",
      arguments: { enabled: nextVal },
    });
    const data = res?.structuredContent as ToggleInterceptNativePayload | undefined;
    if (data) {
      if (typeof data.intercept_native_subagents === "boolean") {
        interceptNativeSubagents = data.intercept_native_subagents;
      } else if (typeof data.enabled === "boolean") {
        interceptNativeSubagents = data.enabled;
      }
      if (workbenchDraft) {
        workbenchDraft.intercept_native_subagents = interceptNativeSubagents;
      }
    }
    render();
    showToast(
      interceptNativeSubagents
        ? "已启用默认接管原生子代理路由"
        : "已改为仅按需使用 Codex（不自动拦截原生子代理）",
    );
  } catch (err) {
    interceptNativeSubagents = prevVal;
    if (workbenchDraft) {
      workbenchDraft.intercept_native_subagents = prevVal;
    }
    render();
    showToast(`切换路由接管开关失败: ${String(err)}`);
  }
}

function populateHistoryTimelineFromRun(run: RunSnapshot | null): void {
  historyTimeline = Array.isArray(run?.events)
    ? run.events.filter((e) => e.kind !== "session" && e.kind !== "turn_done")
    : [];
  historyExpandedCmdIds = new Set();
  for (const ev of historyTimeline) {
    if (
      ev.kind === "command" &&
      typeof ev.item_id === "string" &&
      typeof ev.exit_code === "number" &&
      ev.exit_code !== 0
    ) {
      historyExpandedCmdIds.add(ev.item_id);
    }
  }
  historyGroupOpenOverrides = new Map();
  historySavedScrollTop = 0;
}

function extractToolErrorMessage(res: unknown, fallback: string): string {
  const r = res as { isError?: boolean; content?: Array<{ text?: string }> } | undefined;
  const text = r?.content?.[0]?.text;
  if (typeof text === "string" && text.trim()) {
    return text.trim();
  }
  return fallback;
}

async function refreshHistory(focusRunId?: string, queueIfBusy = false): Promise<void> {
  if (historyInFlight) {
    if (queueIfBusy) {
      historyPendingRefresh = { focusRunId };
    }
    return;
  }
  historyInFlight = true;
  historyLoading = true;
  historyLoadError = null;
  if (!historyPayload && activeTab === "history") {
    render();
  }
  try {
    const res = await app.callServerTool({
      name: "get_history",
      arguments: {
        query: historyQuery,
        filter: historyFilter,
        limit: 80,
      },
    });
    const data = res?.structuredContent as HistoryPayload | undefined;
    if ((res as { isError?: boolean } | undefined)?.isError || !data) {
      historyLoadError = extractToolErrorMessage(res, "历史对话接口返回空数据");
    } else {
      historyPayload = data;
      if (typeof data.retention_hours === "number") {
        retentionHours = data.retention_hours;
      }
      const sessions = data.sessions || [];
      if (openSessionIds.size === 0 && sessions.length > 0) {
        openSessionIds.add(sessions[0].session_id);
      }
      if (focusRunId) {
        historySelectedRunId = focusRunId;
        historyInspecting = true;
        const owner = sessions.find((s) => (s.runs || []).some((r) => r.run_id === focusRunId));
        if (owner) openSessionIds.add(owner.session_id);
      }
      const flatRuns = sessions.flatMap((s) => s.runs || []);
      if (!historySelectedRunId || !flatRuns.some((r) => r.run_id === historySelectedRunId)) {
        historySelectedRunId = flatRuns[0]?.run_id || null;
      }
      if (historySelectedRunId) {
        const target = flatRuns.find((r) => r.run_id === historySelectedRunId) || null;
        if (target && !target.expired) {
          await loadHistoryRunDetail(target.run_id);
          return;
        }
        historySelectedRunDetail = target;
        populateHistoryTimelineFromRun(null);
      } else {
        historySelectedRunDetail = null;
        populateHistoryTimelineFromRun(null);
      }
    }
  } catch (err) {
    historyLoadError = err instanceof Error ? err.message : String(err);
  } finally {
    historyInFlight = false;
    historyLoading = false;
    if (activeTab === "history" && viewMode === "panel") {
      render();
    }
    const pending = historyPendingRefresh;
    historyPendingRefresh = null;
    if (pending) {
      void refreshHistory(pending.focusRunId, true);
    }
  }
}

async function loadHistoryRunDetail(runId: string): Promise<void> {
  const detailToken = ++historyDetailSeq;
  historySelectedRunId = runId;
  const flatRuns = (historyPayload?.sessions || []).flatMap((s) => s.runs || []);
  const summaryRun = flatRuns.find((r) => r.run_id === runId) || null;
  if (summaryRun?.expired) {
    historySelectedRunDetail = summaryRun;
    populateHistoryTimelineFromRun(null);
    if (activeTab === "history" && viewMode === "panel") {
      render();
    }
    return;
  }
  try {
    const res = await app.callServerTool({
      name: "get_run_events",
      arguments: { run_id: runId, since_seq: 0 },
    });
    if (detailToken !== historyDetailSeq || historySelectedRunId !== runId) return;
    const detail = res?.structuredContent as RunSnapshot | undefined;
    if (detail) {
      historySelectedRunDetail = detail;
      populateHistoryTimelineFromRun(detail);
    } else {
      historySelectedRunDetail = summaryRun;
      populateHistoryTimelineFromRun(summaryRun);
    }
  } catch {
    if (detailToken !== historyDetailSeq || historySelectedRunId !== runId) return;
    historySelectedRunDetail = summaryRun;
    populateHistoryTimelineFromRun(summaryRun);
  }
  if (activeTab === "history" && viewMode === "panel") {
    render();
  }
}

async function refreshStats(queueIfBusy = false): Promise<void> {
  if (statsInFlight) {
    if (queueIfBusy) {
      statsPendingReload = true;
    }
    return;
  }
  statsInFlight = true;
  statsLoading = true;
  statsLoadError = null;
  if (!statsPayload && activeTab === "stats") {
    render();
  }
  try {
    const res = await app.callServerTool({
      name: "get_stats",
      arguments: { scope: "antigravity", range: statsRange },
    });
    const data = res?.structuredContent as StatsPayload | undefined;
    if ((res as { isError?: boolean } | undefined)?.isError || !data) {
      statsLoadError = extractToolErrorMessage(res, "统计接口返回空数据");
    } else {
      statsPayload = data;
    }
  } catch (err) {
    statsLoadError = err instanceof Error ? err.message : String(err);
  } finally {
    statsInFlight = false;
    statsLoading = false;
    if (activeTab === "stats" && viewMode === "panel") {
      render();
    }
    if (statsPendingReload) {
      statsPendingReload = false;
      void refreshStats(true);
    }
  }
}

async function refreshWorkbenchConfig(keepDraft = false): Promise<void> {
  if (modelsInFlight) return;
  modelsInFlight = true;
  modelsLoading = true;
  modelsLoadError = null;
  if (!workbenchData && activeTab === "models" && viewMode === "panel") {
    render();
  }
  try {
    const res = await app.callServerTool({
      name: "get_workbench_config",
      arguments: { include_hidden: false },
    });
    const data = res?.structuredContent as WorkbenchPayload | undefined;
    if ((res as { isError?: boolean } | undefined)?.isError || !data || !data.workbench_config) {
      modelsLoadError = extractToolErrorMessage(res, "配置服务返回空数据");
    } else {
      workbenchData = data;
      maxConcurrentRuns = data.max_concurrent_runs || maxConcurrentRuns;
      if (typeof data.retention_hours === "number") retentionHours = data.retention_hours;
      if (typeof data.enabled === "boolean") codexEnabled = data.enabled;
      if (typeof data.intercept_native_subagents === "boolean") {
        interceptNativeSubagents = data.intercept_native_subagents;
      }
      if (typeof data.exclude_orchestrator_models === "boolean") {
        excludeOrchestratorModels = data.exclude_orchestrator_models;
      }
      if (!keepDraft || !workbenchDraft) {
        workbenchDraft = cloneConfig(data.workbench_config);
      }
    }
  } catch (err) {
    modelsLoadError = err instanceof Error ? err.message : String(err);
  } finally {
    modelsInFlight = false;
    modelsLoading = false;
    if (activeTab === "models" && viewMode === "panel") {
      render();
    }
  }
}

function syncDraftFromDom(): void {
  if (!workbenchDraft) return;
  const val = (id: string) =>
    (document.getElementById(id) as HTMLInputElement | HTMLSelectElement | null)?.value ?? "";
  const checked = (id: string) =>
    Boolean((document.getElementById(id) as HTMLInputElement | null)?.checked);

  if (document.getElementById("cfg-def-model")) {
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
    workbenchDraft.mapping.native_passthrough_subagents = val("cfg-map-passthrough-subagents")
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
    const rh = parseInt(val("cfg-map-retention-hours"), 10);
    if (!Number.isNaN(rh)) {
      workbenchDraft.mapping.retention_hours = Math.max(1, Math.min(168, rh));
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
  const cwd =
    (activeTab === "history" ? historySelectedRunDetail?.cwd : currentRun?.cwd) || ".";
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
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(filePath).catch(() => {});
      showToast(`已复制路径: ${filePath}`);
    }
  }
}

function switchTab(tab: PanelTab, focusHistoryRunId?: string): void {
  activeTab = tab;
  if (tab === "history") {
    newArchiveBadge = false;
    historyInspecting = Boolean(focusHistoryRunId);
  }
  syncPollingTimers();
  render();
  if (tab === "subagents") void refreshMonitorRuns();
  else if (tab === "history") void refreshHistory(focusHistoryRunId, true);
  else if (tab === "stats") void refreshStats(true);
  else if (tab === "models") void refreshWorkbenchConfig();
}

function getMonitorTraceViewState(): TraceViewState {
  return {
    timelineEntries: monitorTimeline,
    expandedCmdIds: monitorExpandedCmdIds,
    groupOpenOverrides: monitorGroupOpenOverrides,
    traceExpandedOverrides,
    promptExpandedIds,
    provenanceExpandedIds,
    composerDrafts,
    followupSending,
    userPinnedToBottom: monitorUserPinnedToBottom,
    canSendMessage: canSendMessage(),
    retentionHours,
    actionFeedback,
  };
}

function getHistoryTraceViewState(): TraceViewState {
  return {
    timelineEntries: historyTimeline,
    expandedCmdIds: historyExpandedCmdIds,
    groupOpenOverrides: historyGroupOpenOverrides,
    traceExpandedOverrides,
    promptExpandedIds,
    provenanceExpandedIds,
    composerDrafts,
    followupSending: false,
    userPinnedToBottom: false,
    canSendMessage: false,
    retentionHours,
    actionFeedback: null,
  };
}

function setupPopoverAndInterestSupport(): void {
  if (popoverHintFallbackBound) return;
  popoverHintFallbackBound = true;
  const probe = document.createElement("div");
  probe.popover = "hint";
  if (probe.popover !== "hint") {
    document.addEventListener(
      "beforetoggle",
      (ev) => {
        const target = ev.target as HTMLElement | null;
        if (!target || !target.getAttribute) return;
        const isToggletip =
          target.getAttribute("popover") === "hint" &&
          document.querySelector(`[popovertarget="${CSS.escape(target.id)}"]`);
        if ((ev as ToggleEvent).newState === "open" && isToggletip) {
          target.popover = "auto";
        }
      },
      true,
    );
  }

  document.addEventListener(
    "toggle",
    (ev) => {
      const pop = ev.target as HTMLElement | null;
      if (!pop || !pop.id) return;
      const triggers = document.querySelectorAll(
        `[interestfor="${CSS.escape(pop.id)}"][popovertarget]`,
      );
      const isOpen = (ev as ToggleEvent).newState === "open";
      triggers.forEach((btn) => {
        btn.setAttribute("aria-expanded", isOpen ? "true" : "false");
      });
    },
    true,
  );
}

function positionTipFallback(trigger: HTMLElement, tip: HTMLElement): void {
  if (typeof CSS !== "undefined" && CSS.supports && CSS.supports("position-area: block-end")) {
    return;
  }
  const rect = trigger.getBoundingClientRect();
  const tipW = Math.min(280, window.innerWidth - 24);
  tip.style.position = "fixed";
  tip.style.margin = "0";
  tip.style.inset = "auto";
  tip.style.maxWidth = `${tipW}px`;
  const left = Math.min(Math.max(12, rect.left - 8), window.innerWidth - tipW - 12);
  let top = rect.bottom + 6;
  if (top + 120 > window.innerHeight) {
    top = Math.max(12, rect.top - 116);
  }
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}

function wireHelpTips(container: HTMLElement): void {
  const supportsInterest = "interestForElement" in HTMLButtonElement.prototype;
  container.querySelectorAll<HTMLElement>("[interestfor]").forEach((trigger) => {
    const tipId = trigger.getAttribute("interestfor");
    if (!tipId) return;
    const tip = document.getElementById(tipId) as (HTMLElement & {
      showPopover?: () => void;
      hidePopover?: () => void;
      _hoverWired?: boolean;
    }) | null;
    if (!tip) return;
    if (!trigger.hasAttribute("aria-expanded")) {
      trigger.setAttribute("aria-expanded", "false");
    }
    if (supportsInterest) return;

    const openTip = () => {
      if (interestCloseTimer !== null) {
        window.clearTimeout(interestCloseTimer);
        interestCloseTimer = null;
      }
      if (interestOpenTimer !== null) window.clearTimeout(interestOpenTimer);
      const delay = interestPolyfillActiveTip ? 0 : 120;
      interestOpenTimer = window.setTimeout(() => {
        if (interestPolyfillActiveTip && interestPolyfillActiveTip !== tip) {
          try {
            (interestPolyfillActiveTip as { hidePopover?: () => void }).hidePopover?.();
          } catch {
            // Ignore
          }
        }
        positionTipFallback(trigger, tip);
        try {
          if (typeof tip.showPopover === "function") tip.showPopover();
          else tip.classList.add(":popover-open");
        } catch {
          // Ignore
        }
        trigger.classList.add("interest-source");
        tip.classList.add("interest-target");
        interestPolyfillActiveTip = tip;
      }, delay);
    };

    const scheduleClose = () => {
      if (interestOpenTimer !== null) {
        window.clearTimeout(interestOpenTimer);
        interestOpenTimer = null;
      }
      if (interestCloseTimer !== null) window.clearTimeout(interestCloseTimer);
      interestCloseTimer = window.setTimeout(() => {
        try {
          if (typeof tip.hidePopover === "function") tip.hidePopover();
          else tip.classList.remove(":popover-open");
        } catch {
          // Ignore
        }
        trigger.classList.remove("interest-source");
        tip.classList.remove("interest-target");
        if (interestPolyfillActiveTip === tip) interestPolyfillActiveTip = null;
      }, 160);
    };

    trigger.addEventListener("mouseenter", openTip);
    trigger.addEventListener("focus", openTip);
    trigger.addEventListener("mouseleave", scheduleClose);
    trigger.addEventListener("blur", scheduleClose);
    trigger.addEventListener("click", () => {
      positionTipFallback(trigger, tip);
    });

    if (!tip._hoverWired) {
      tip._hoverWired = true;
      tip.addEventListener("mouseenter", () => {
        if (interestCloseTimer !== null) {
          window.clearTimeout(interestCloseTimer);
          interestCloseTimer = null;
        }
      });
      tip.addEventListener("mouseleave", scheduleClose);
    }
  });
}

function disconnectAllTablistObservers(): void {
  for (const obs of tablistObservers.values()) {
    obs.disconnect();
  }
  tablistObservers.clear();
}

function attachTablistOverflowHints(tablist: HTMLElement | null): void {
  if (!tablist) return;
  const key = tablist.id || "__default__";
  const prev = tablistObservers.get(key);
  if (prev) {
    prev.disconnect();
    tablistObservers.delete(key);
  }
  if (typeof CSS !== "undefined" && CSS.supports && CSS.supports("animation-timeline", "scroll()")) {
    return;
  }
  const first = tablist.firstElementChild;
  const last = tablist.lastElementChild;
  if (!first || !last) {
    tablist.classList.remove("more-start", "more-end");
    return;
  }
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.target === first) {
          tablist.classList.toggle("more-start", entry.intersectionRatio < 1);
        }
        if (entry.target === last) {
          tablist.classList.toggle("more-end", entry.intersectionRatio < 1);
        }
      }
    },
    { root: tablist, threshold: 1, rootMargin: "1px" },
  );
  observer.observe(first);
  observer.observe(last);
  tablistObservers.set(key, observer);
}

function render(): void {
  const root = document.getElementById("root");
  if (!root) return;

  const existingBox = document.getElementById("timeline-box");
  if (existingBox) {
    if (viewMode === "panel" && activeTab === "history") {
      historySavedScrollTop = existingBox.scrollTop;
    } else {
      monitorSavedScrollTop = existingBox.scrollTop;
      monitorUserPinnedToBottom =
        existingBox.scrollHeight - existingBox.scrollTop - existingBox.clientHeight < 36;
    }
  }

  disconnectAllTablistObservers();

  root.className = viewMode === "panel" ? "app is-panel" : "app is-inline";

  if (viewMode === "inline") {
    if (!currentRun) {
      root.innerHTML = `<div class="empty-hint" style="padding:12px">等待 Codex Worker 启动...</div>`;
      bindInteractions();
      return;
    }
    root.innerHTML = renderTraceView(currentRun, getMonitorTraceViewState(), { readOnly: false });
    bindInteractions();
    restoreTimelineScroll();
    return;
  }

  const isMonitor = activeTab === "subagents";
  const subTitleMap: Record<PanelTab, string> = {
    subagents: "活跃监控",
    history: "历史对话 (Antigravity 大会话)",
    stats: "统计面板",
    models: "模型与配置",
  };

  const narrowTablistHtml = renderMonitorTablist(panelRuns, activeRunId, "narrow", retentionHours);
  const wideTablistHtml = renderMonitorTablist(panelRuns, activeRunId, "wide", retentionHours);

  const topbarHtml = `<div class="topbar">
    <div id="narrow-subpage-bar" class="narrow-subpage-bar${isMonitor ? " is-hidden" : ""}">
      <button type="button" id="narrow-back-btn" class="btn btn-sm">← 返回监控</button>
      <span class="narrow-subpage-title">${escapeHtml(subTitleMap[activeTab])}</span>
    </div>

    <div id="narrow-tabs-wrap" class="narrow-tabs-wrap${isMonitor ? "" : " is-hidden"}">
      <div id="narrow-agent-tablist" class="agent-tablist" role="tablist" aria-label="活跃子 Agent 标签">${narrowTablistHtml}</div>
    </div>

    <div class="wide-only-nav wide-top-nav" role="tablist" aria-label="控制中心主视图">
      <button type="button" role="tab" data-nav-tab="subagents" aria-selected="${activeTab === "subagents" ? "true" : "false"}" class="wide-view-tab">活跃监控</button>
      <button type="button" role="tab" data-nav-tab="history" aria-selected="${activeTab === "history" ? "true" : "false"}" class="wide-view-tab">
        <span>历史对话</span>
        ${closed24hCount > 0 ? `<span class="tab-count-pill tabular-nums">${closed24hCount}</span>` : ""}
      </button>
      <button type="button" role="tab" data-nav-tab="stats" aria-selected="${activeTab === "stats" ? "true" : "false"}" class="wide-view-tab">统计面板</button>
      <button type="button" role="tab" data-nav-tab="models" aria-selected="${activeTab === "models" ? "true" : "false"}" class="wide-view-tab">模型与配置</button>
    </div>

    <div class="topbar-right">
      ${renderCodexToggleButton(codexEnabled, "topbar")}

      <span class="slot-pill tabular-nums" title="当前并发槽位使用情况">
        <span class="status-dot ${activeRunsCount > 0 ? "is-running" : "is-done"}" aria-hidden="true"></span>
        <span>${activeRunsCount}/${maxConcurrentRuns}</span>
      </span>

      <button type="button" data-nav-tab="history" class="narrow-only-nav icon-btn" title="历史对话 (按 Antigravity 大会话分组)" aria-label="打开历史对话">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M2.5 3.5h11a1 1 0 011 1v6a1 1 0 01-1 1H6l-3.5 2V4.5a1 1 0 011-1z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
        </svg>
        ${newArchiveBadge ? `<span class="badge-dot" aria-hidden="true"></span>` : ""}
      </button>

      <button type="button" data-nav-tab="stats" class="narrow-only-nav icon-btn" title="统计面板" aria-label="打开统计面板">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M3 13V9.5M8 13V4M13 13V7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
        </svg>
      </button>

      <button type="button" data-nav-tab="models" class="narrow-only-nav icon-btn" title="模型与配置" aria-label="打开模型与配置">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M8 10a2 2 0 100-4 2 2 0 000 4z" stroke="currentColor" stroke-width="1.4" />
          <path d="M13.2 8a5.2 5.2 0 00-.08-.9l1.2-.92-1.15-2-1.42.45a5.3 5.3 0 00-1.55-.9L9.9 2.3H7.6l-.3 1.43c-.56.2-1.08.5-1.55.9L4.33 4.18l-1.15 2 1.2.92a5.2 5.2 0 000 1.8l-1.2.92 1.15 2 1.42-.45c.47.4.99.7 1.55.9l.3 1.43h2.3l.3-1.43c.56-.2 1.08-.5 1.55-.9l1.42.45 1.15-2-1.2-.92c.05-.3.08-.6.08-.9z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" />
        </svg>
      </button>

      <button type="button" id="btn-archive-all" class="icon-btn" title="归档所有已完成标签" aria-label="清空已完成标签">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M2.5 4.5h11M5.5 4.5V3h5v1.5M4.5 7l.6 6h5.8l.6-6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>

      <button type="button" id="panel-refresh-btn" class="icon-btn" title="手动刷新当前视图" aria-label="刷新">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M13.5 8a5.5 5.5 0 1 1-1.4-3.65M13.5 2.5v2.75h-2.75" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
    </div>
  </div>`;

  const pausedBannerHtml = !codexEnabled
    ? `<div id="paused-banner" class="paused-banner">
        <span>Codex 子代理已暂停，新子任务将直接交由 Antigravity 原生子代理执行（现有日志仍可查看）。</span>
        <button type="button" id="banner-resume-btn" class="btn btn-sm">重新开启</button>
      </div>`
    : "";

  const wideStripHtml = isMonitor
    ? `<div id="wide-subagent-strip" class="wide-only-nav wide-subagent-strip">
        <div id="wide-agent-tablist" class="agent-tablist" role="tablist" aria-label="活跃子 Agent 对话标签">${wideTablistHtml}</div>
        <div class="wide-subagent-right">
          <span class="wide-subagent-hint">Antigravity 联动子 Agent (留存 ${retentionHours}h)</span>
          ${renderCodexToggleButton(codexEnabled, "inline")}
        </div>
      </div>`
    : "";

  let bodyHtml = "";
  if (activeTab === "subagents") {
    bodyHtml = renderMonitorView({
      runs: panelRuns,
      activeRunId,
      currentRun,
      traceState: getMonitorTraceViewState(),
      retentionHours,
      enabled: codexEnabled,
    });
  } else if (activeTab === "history") {
    bodyHtml = renderHistoryView({
      query: historyQuery,
      filter: historyFilter,
      payload: historyPayload,
      loading: historyLoading,
      loadError: historyLoadError,
      selectedRunId: historySelectedRunId,
      selectedRunDetail: historySelectedRunDetail,
      inspecting: historyInspecting,
      openSessionIds,
      traceState: getHistoryTraceViewState(),
      retentionHours,
      enabled: codexEnabled,
    });
  } else if (activeTab === "stats") {
    bodyHtml = renderStatsView({
      range: statsRange,
      stats: statsPayload,
      loading: statsLoading,
      loadError: statsLoadError,
      enabled: codexEnabled,
    });
  } else {
    bodyHtml = renderModelsView({
      data: workbenchData,
      draft: workbenchDraft,
      preview: previewPayload,
      searchQuery: modelSearchQuery,
      loading: modelsLoading,
      loadError: modelsLoadError,
      saving: savingConfig,
      enabled: codexEnabled,
      interceptNativeSubagents,
      excludeOrchestratorModels,
      retentionHours,
      statusMsg: configStatusMsg,
      errorMsg: configErrorMsg,
    });
  }

  root.innerHTML = `${topbarHtml}${pausedBannerHtml}${wideStripHtml}<div class="panel-body">${bodyHtml}</div>`;
  bindInteractions();
  restoreTimelineScroll();
}

function restoreTimelineScroll(): void {
  const box = document.getElementById("timeline-box");
  if (!box) return;
  if (viewMode === "panel" && activeTab === "history") {
    box.scrollTop = historySavedScrollTop;
  } else if (monitorUserPinnedToBottom && currentRun?.status === "running" && (viewMode === "inline" || activeTab === "subagents")) {
    box.scrollTop = box.scrollHeight;
    monitorSavedScrollTop = box.scrollTop;
  } else {
    box.scrollTop = monitorSavedScrollTop;
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
  setupPopoverAndInterestSupport();

  // Topbar navigation
  document.querySelectorAll<HTMLElement>("[data-nav-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.getAttribute("data-nav-tab") as PanelTab | null;
      if (!tab || tab === activeTab) return;
      switchTab(tab);
    });
  });

  document.getElementById("narrow-back-btn")?.addEventListener("click", () => {
    switchTab("subagents");
  });

  document.getElementById("panel-refresh-btn")?.addEventListener("click", () => {
    if (activeTab === "subagents") void refreshMonitorRuns();
    else if (activeTab === "history") void refreshHistory(undefined, true);
    else if (activeTab === "stats") void refreshStats(true);
    else if (activeTab === "models") void refreshWorkbenchConfig();
  });

  document.querySelectorAll<HTMLElement>("[data-toggle-codex-master]").forEach((btn) => {
    btn.addEventListener("click", () => {
      void handleToggleCodexEnabled(!codexEnabled);
    });
  });

  document.getElementById("retry-models-btn")?.addEventListener("click", () => {
    void refreshWorkbenchConfig();
  });

  document.getElementById("retry-history-btn")?.addEventListener("click", () => {
    void refreshHistory(undefined, true);
  });

  document.getElementById("retry-stats-btn")?.addEventListener("click", () => {
    void refreshStats(true);
  });

  document.getElementById("banner-resume-btn")?.addEventListener("click", () => {
    void handleToggleCodexEnabled(true);
  });

  document.getElementById("btn-archive-all")?.addEventListener("click", () => {
    void handleCloseAllFinished();
  });

  document.getElementById("empty-to-history")?.addEventListener("click", () => {
    switchTab("history");
  });

  // Monitor tablist interactions
  document.querySelectorAll<HTMLElement>('[role="tab"].agent-tab-btn').forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-run-id");
      if (id && id !== activeRunId) {
        void selectRun(id);
      }
    });
    btn.addEventListener("keydown", (ev) => {
      const list = btn.closest(".agent-tablist");
      if (!list) return;
      const tabs = Array.from(list.querySelectorAll<HTMLElement>('[role="tab"].agent-tab-btn'));
      const idx = tabs.indexOf(btn);
      if (idx < 0 || tabs.length === 0) return;
      let nextIdx = -1;
      if (ev.key === "ArrowRight") nextIdx = (idx + 1) % tabs.length;
      else if (ev.key === "ArrowLeft") nextIdx = (idx - 1 + tabs.length) % tabs.length;
      else if (ev.key === "Home") nextIdx = 0;
      else if (ev.key === "End") nextIdx = tabs.length - 1;
      if (nextIdx >= 0) {
        ev.preventDefault();
        const nextId = tabs[nextIdx].getAttribute("data-run-id");
        if (nextId) {
          void selectRun(nextId).then(() => {
            const focused = list.querySelector<HTMLElement>(`[data-run-id="${CSS.escape(nextId)}"]`);
            focused?.focus();
            focused?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
          });
        }
      }
    });
  });

  attachTablistOverflowHints(document.getElementById("narrow-agent-tablist"));
  attachTablistOverflowHints(document.getElementById("wide-agent-tablist"));

  document.querySelectorAll<HTMLElement>("[data-close-run]").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const id = btn.getAttribute("data-close-run");
      if (id) void handleCloseRun(id);
    });
  });

  // Trace interactions (routed cleanly between Monitor & History)
  const timelineBox = document.getElementById("timeline-box");
  timelineBox?.addEventListener("scroll", () => {
    if (viewMode === "panel" && activeTab === "history") {
      historySavedScrollTop = timelineBox.scrollTop;
    } else {
      monitorSavedScrollTop = timelineBox.scrollTop;
      monitorUserPinnedToBottom =
        timelineBox.scrollHeight - timelineBox.scrollTop - timelineBox.clientHeight < 36;
    }
    updateJumpButtonVisibility(timelineBox);
  });

  document.getElementById("jump-latest-btn")?.addEventListener("click", () => {
    monitorUserPinnedToBottom = true;
    const box = document.getElementById("timeline-box");
    if (box) {
      box.scrollTop = box.scrollHeight;
      monitorSavedScrollTop = box.scrollTop;
      updateJumpButtonVisibility(box);
    }
  });

  document.querySelectorAll<HTMLElement>("[data-toggle-prov]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-toggle-prov");
      if (!id) return;
      if (provenanceExpandedIds.has(id)) provenanceExpandedIds.delete(id);
      else provenanceExpandedIds.add(id);
      render();
    });
  });

  document.querySelectorAll<HTMLElement>("[data-toggle-prompt]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-toggle-prompt");
      if (!id) return;
      if (promptExpandedIds.has(id)) promptExpandedIds.delete(id);
      else promptExpandedIds.add(id);
      render();
    });
  });

  document.querySelectorAll<HTMLElement>("[data-toggle-trace]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-toggle-trace");
      if (!id) return;
      const targetRun =
        viewMode === "panel" && activeTab === "history" ? historySelectedRunDetail : currentRun;
      const defaultOpen =
        targetRun?.status === "running" ||
        targetRun?.status === "partial" ||
        targetRun?.status === "blocked" ||
        targetRun?.status === "error" ||
        targetRun?.status === "cancelled";
      const curOpen = traceExpandedOverrides.has(id)
        ? traceExpandedOverrides.get(id)!
        : Boolean(defaultOpen);
      traceExpandedOverrides.set(id, !curOpen);
      render();
    });
  });

  document.querySelectorAll<HTMLElement>("[data-toggle-group]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-toggle-group");
      const curOpen = btn.getAttribute("data-current-open") === "1";
      if (!id) return;
      const targetMap =
        viewMode === "panel" && activeTab === "history"
          ? historyGroupOpenOverrides
          : monitorGroupOpenOverrides;
      targetMap.set(id, !curOpen);
      render();
    });
  });

  document.querySelectorAll<HTMLElement>("[data-toggle-cmd]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-toggle-cmd");
      if (!id) return;
      const targetSet =
        viewMode === "panel" && activeTab === "history"
          ? historyExpandedCmdIds
          : monitorExpandedCmdIds;
      if (targetSet.has(id)) targetSet.delete(id);
      else targetSet.add(id);
      render();
    });
  });

  document.querySelectorAll<HTMLElement>("[data-open-file]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const filePath = btn.getAttribute("data-open-file");
      if (filePath) void openFileInAuxPane(filePath);
    });
  });

  document.getElementById("stop-run-btn")?.addEventListener("click", async () => {
    const id = activeRunId;
    if (!id) return;
    try {
      await app.callServerTool({ name: "cancel_run", arguments: { run_id: id } });
      await fetchIncrementalEvents();
      showToast("已发送停止信号");
    } catch {
      showToast("停止任务失败");
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

  document.querySelectorAll<HTMLInputElement>("[data-followup-input]").forEach((inp) => {
    inp.addEventListener("input", () => {
      const id = inp.getAttribute("data-followup-input");
      if (id) composerDrafts.set(id, inp.value);
    });
  });

  document.querySelectorAll<HTMLFormElement>("[data-followup-form]").forEach((form) => {
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const id = form.getAttribute("data-followup-form");
      if (!id || followupSending) return;
      const prompt = (composerDrafts.get(id) || "").trim();
      if (!prompt) return;
      followupSending = true;
      render();
      try {
        const res = await app.callServerTool({
          name: "send_followup",
          arguments: { run_id: id, prompt },
        });
        const newRun = res?.structuredContent as RunSnapshot | undefined;
        composerDrafts.set(id, "");
        if (newRun?.run_id) {
          panelRuns = [newRun, ...panelRuns.filter((r) => r.run_id !== newRun.run_id)];
          activeRunsCount = panelRuns.filter((r) => r.status === "running").length;
          activeTab = "subagents";
          resetRunTimeline(newRun);
          syncPollingTimers();
          render();
          showToast(`已发起追问，切换至新执行「${getRunShortTitle(newRun)}」`);
        }
      } catch (err) {
        showToast(`追问发送失败: ${String(err)}`);
      } finally {
        followupSending = false;
        render();
      }
    });
  });

  document.querySelectorAll<HTMLElement>("[data-reopen-run]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-reopen-run");
      if (id) void handleReopenRun(id);
    });
  });

  document.querySelectorAll<HTMLElement>("[data-jump-active]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-jump-active");
      if (id) {
        activeTab = "subagents";
        void selectRun(id);
      }
    });
  });

  // History view interactions
  const histSearch = document.getElementById("history-search-input") as HTMLInputElement | null;
  if (histSearch) {
    histSearch.addEventListener("input", () => {
      const pos = histSearch.selectionStart ?? histSearch.value.length;
      historyQuery = histSearch.value;
      if (historySearchTimer !== null) window.clearTimeout(historySearchTimer);
      historySearchTimer = window.setTimeout(() => {
        void refreshHistory(undefined, true).then(() => {
          const again = document.getElementById("history-search-input") as HTMLInputElement | null;
          if (again) {
            again.focus();
            again.setSelectionRange(pos, pos);
          }
        });
      }, 220);
    });
  }

  document.querySelectorAll<HTMLElement>("[data-hfilter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const f = btn.getAttribute("data-hfilter") as HistoryFilter | null;
      if (!f) return;
      historyFilter = f;
      historyInspecting = false;
      void refreshHistory(undefined, true);
    });
  });

  document.querySelectorAll<HTMLElement>("[data-hselect]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-hselect");
      if (!id) return;
      historyInspecting = true;
      void loadHistoryRunDetail(id);
    });
  });

  document.getElementById("history-back-list")?.addEventListener("click", () => {
    historyInspecting = false;
    render();
  });

  const accRoot = document.getElementById("session-accordion");
  const toggleAllBtn = document.getElementById("acc-toggle-all");
  if (accRoot && toggleAllBtn) {
    accRoot.addEventListener(
      "toggle",
      (ev) => {
        const d = ev.target as HTMLDetailsElement | null;
        if (!d || !d.matches || !d.matches(".session-group")) return;
        const sid = d.getAttribute("data-session-id");
        if (!sid) return;
        if (d.open) openSessionIds.add(sid);
        else openSessionIds.delete(sid);
        const items = Array.from(accRoot.querySelectorAll<HTMLDetailsElement>(":scope > details"));
        const everyOpen = items.length > 0 && items.every((el) => el.open);
        toggleAllBtn.setAttribute("aria-expanded", everyOpen ? "true" : "false");
        toggleAllBtn.textContent = everyOpen ? "全部收起" : "全部展开";
      },
      true,
    );

    toggleAllBtn.addEventListener("click", () => {
      const expand = toggleAllBtn.getAttribute("aria-expanded") !== "true";
      accRoot.querySelectorAll<HTMLDetailsElement>(":scope > details").forEach((d) => {
        d.open = expand;
        const sid = d.getAttribute("data-session-id");
        if (sid) {
          if (expand) openSessionIds.add(sid);
          else openSessionIds.delete(sid);
        }
      });
      toggleAllBtn.setAttribute("aria-expanded", expand ? "true" : "false");
      toggleAllBtn.textContent = expand ? "全部收起" : "全部展开";
    });
  }

  // Stats interactions
  document.querySelectorAll<HTMLElement>("[data-stats-range]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const rg = btn.getAttribute("data-stats-range") as StatsPayload["range"] | null;
      if (rg) {
        statsRange = rg;
        void refreshStats(true);
      }
    });
  });

  // Copy buttons
  document.querySelectorAll<HTMLElement>("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const txt = btn.getAttribute("data-copy") || "";
      try {
        await navigator.clipboard.writeText(txt);
        showToast(`已复制: ${txt.length > 48 ? `${txt.slice(0, 48)}...` : txt}`);
      } catch {
        // Ignore clipboard errors in restricted iframes
      }
    });
  });

  // Models & Config interactions
  if (activeTab === "models") {
    const configRoot = document.querySelector<HTMLElement>(".config-view-root");
    if (configRoot) wireHelpTips(configRoot);
  }

  document.getElementById("cfg-intercept-switch")?.addEventListener("change", (ev) => {
    void handleToggleInterceptNative(Boolean((ev.target as HTMLInputElement).checked));
  });

  document.getElementById("cfg-orch-switch")?.addEventListener("change", (ev) => {
    void handleToggleExcludeOrchestrator(Boolean((ev.target as HTMLInputElement).checked));
  });

  document.getElementById("run-doctor-btn")?.addEventListener("click", async () => {
    configStatusMsg = null;
    configErrorMsg = null;
    try {
      await app.callServerTool({ name: "run_doctor", arguments: {} });
    } catch {
      // Refresh full workbench config regardless
    }
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
        arguments: {
          enabled: codexEnabled,
          intercept_native_subagents: interceptNativeSubagents,
          exclude_orchestrator_models: excludeOrchestratorModels,
          defaults: workbenchDraft.defaults,
          mapping: workbenchDraft.mapping,
          custom_models: workbenchDraft.custom_models,
          excluded_models: workbenchDraft.excluded_models,
          presets: workbenchDraft.presets,
        },
      });
      const data = res?.structuredContent as (WorkbenchPayload & { saved?: WorkbenchConfig }) | undefined;
      if (data) {
        workbenchData = data;
        if (data.saved) workbenchDraft = cloneConfig(data.saved);
        maxConcurrentRuns = data.max_concurrent_runs || maxConcurrentRuns;
        if (typeof data.retention_hours === "number") retentionHours = data.retention_hours;
        if (typeof data.enabled === "boolean") codexEnabled = data.enabled;
        if (typeof data.intercept_native_subagents === "boolean") {
          interceptNativeSubagents = data.intercept_native_subagents;
        }
        if (typeof data.exclude_orchestrator_models === "boolean") {
          excludeOrchestratorModels = data.exclude_orchestrator_models;
        }
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

  document.getElementById("model-search-input")?.addEventListener("input", (ev) => {
    syncDraftFromDom();
    const inp = ev.target as HTMLInputElement;
    const pos = inp.selectionStart ?? inp.value.length;
    modelSearchQuery = inp.value;
    render();
    const el = document.getElementById("model-search-input") as HTMLInputElement | null;
    if (el) {
      el.focus();
      el.setSelectionRange(pos, pos);
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
      prompt: a.prompt,
      prompt_preview: a.prompt.slice(0, 180),
      elapsed_sec: 0,
    };
    resetMonitorTimelineState();
    updateLocalTiming(currentRun);
    render();
  }
};

app.ontoolresult = (result) => {
  const sc = (result?.structuredContent || {}) as Record<string, unknown>;
  if (sc.view === "panel") {
    viewMode = "panel";
    activeTab =
      sc.tab === "history" || sc.tab === "stats" || sc.tab === "models" || sc.tab === "subagents"
        ? sc.tab
        : "subagents";
    panelRuns = Array.isArray(sc.runs) ? (sc.runs as RunSnapshot[]) : [];
    activeRunsCount = Number(sc.active_runs ?? panelRuns.filter((r) => r.status === "running").length);
    closed24hCount = Number(sc.closed_24h_count ?? closed24hCount);
    maxConcurrentRuns = Number(sc.max_concurrent_runs ?? 5);
    if (typeof sc.enabled === "boolean") codexEnabled = sc.enabled;
    if (typeof sc.intercept_native_subagents === "boolean") {
      interceptNativeSubagents = sc.intercept_native_subagents;
    }
    if (typeof sc.exclude_orchestrator_models === "boolean") {
      excludeOrchestratorModels = sc.exclude_orchestrator_models;
    }
    if (typeof sc.retention_hours === "number") retentionHours = sc.retention_hours;

    const targetId =
      typeof sc.selected_run_id === "string" && sc.selected_run_id
        ? sc.selected_run_id
        : panelRuns[0]?.run_id || null;

    if (activeTab === "history") {
      newArchiveBadge = false;
      syncPollingTimers();
      render();
      void refreshHistory(undefined, true);
    } else if (activeTab === "stats") {
      syncPollingTimers();
      render();
      void refreshStats(true);
    } else if (activeTab === "models") {
      syncPollingTimers();
      render();
      void refreshWorkbenchConfig();
    } else {
      if (targetId) {
        activeRunId = targetId;
        currentRun = panelRuns.find((r) => r.run_id === targetId) || null;
        if (currentRun) updateLocalTiming(currentRun);
        render();
        void (async () => {
          await selectRun(targetId);
          scheduleNextMonitorListRefresh();
        })();
      } else {
        activeRunId = null;
        currentRun = null;
        resetMonitorTimelineState();
        syncPollingTimers();
        render();
      }
    }
    return;
  }

  if (typeof sc.run_id === "string") {
    viewMode = "inline";
    stopPanelRefresh();
    resetRunTimeline(sc as unknown as RunSnapshot);
    syncPollingTimers();
    render();
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

function handlePageWakeup(): void {
  if (document.hidden) return;
  const now = Date.now();
  if (now - lastWakeupMs < 250) return;
  lastWakeupMs = now;

  syncPollingTimers();
  if (viewMode === "inline") {
    if (activeRunId && currentRun?.status === "running") {
      void fetchIncrementalEvents();
    }
    return;
  }

  if (activeTab === "subagents") {
    if (activeRunId && currentRun?.status === "running") {
      void fetchIncrementalEvents();
    }
    void refreshMonitorRuns();
  } else if (activeTab === "history") {
    void refreshHistory();
  } else if (activeTab === "stats") {
    void refreshStats();
  } else if (activeTab === "models") {
    void refreshWorkbenchConfig(true);
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopPolling();
    stopPanelRefresh();
  } else {
    handlePageWakeup();
  }
});

window.addEventListener("focus", () => {
  if (!document.hidden) {
    handlePageWakeup();
  }
});

async function init(): Promise<void> {
  await app.connect();
  const ctx = app.getHostContext();
  if (ctx?.theme) applyDocumentTheme(ctx.theme);
  if (ctx?.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
}

void init();
