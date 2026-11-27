import { App, applyDocumentTheme, applyHostStyleVariables } from "@modelcontextprotocol/ext-apps";
import { z } from "zod";
import type {
  CloseRunPayload,
  CommandPreviewPayload,
  HistoryPayload,
  ListRunsPayload,
  RunSnapshot,
  StartUserTaskPayload,
  StatsPayload,
  TimelineEvent,
  UpdateCheckPayload,
  UpdateStatusPayload,
  WorkbenchConfig,
  WorkbenchPayload,
} from "./types";
import { escapeHtml, formatDuration, getRunShortTitle, renderCodexToggleButton } from "./utils";
import { renderHistoryView, type HistoryFilter } from "./views/history";
import { isUpdateRunning, MCP_REFRESH_GUIDANCE, remoteUpdateLabel, renderModelsView, renderUpdateCard, updateBlockReason } from "./views/models";
import { renderMonitorTablist, renderMonitorView, renderNarrowAgentSwitcher, type UserTaskDraft } from "./views/monitor";
import { renderStatsView } from "./views/stats";
import { renderTraceView, type TraceViewState } from "./views/trace";

const AuxPaneResponseSchema = z.object({ isError: z.boolean().optional() }).passthrough();

const app = new App({ name: "Agy Codex", version: "1.5.0" }, {}, { autoResize: false });

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
let globalActiveRuns = 0;
let totalUnclosedRuns = 0;
let otherSessionsRuns = 0;
let scopeSessionId: string | null = null;
let scopeSessionTitle: string | null = null;
let scopeMode: "session" | "all" = "all";
let closed24hCount = 0;
let newArchiveBadge = false;
let maxConcurrentRuns = 5;

// Monitor / active trace state (strictly isolated from History!)
let panelRuns: RunSnapshot[] = [];
let narrowPickerOpen = false;
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
let defaultCwd = "";
let manualRefreshing = false;
let monitorRunMutationSeq = 0;
let composerOpen = false;
let composerSending = false;
let composerError: string | null = null;
let composerCwdEdited = false;
let composerDraft = blankUserTaskDraft();
let composerTriggerId = "";

// Timers & strict per-endpoint in-flight guards (zero polling when hidden!)
let pollTimer: number | null = null;
let tickTimer: number | null = null;
let panelRefreshTimer: number | null = null;
let pollInFlight = false;
let listInFlight = false;
let selectRunSeq = 0;
let monitorSelectionPending = false;
let resourcesActive = true;
let appConnected = false;
let disconnectSizeObserver: (() => void) | null = null;
let historyDetailSeq = 0;
const monitorRefresh = createRefreshQueue();
const historyRefresh = createRefreshQueue();
const statsRefresh = createRefreshQueue();
const configRefresh = createRefreshQueue();
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
let historyTimelineRunId: string | null = null;
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
let configMutationSeq = 0;
let configWritePending = false;
let savingConfig = false;
let configStatusMsg: string | null = null;
let configErrorMsg: string | null = null;

// 插件更新状态独立于配置草稿与各标签的数据加载。
let updateCheck: UpdateCheckPayload | null = null;
let updateStatus: UpdateStatusPayload | null = null;
let updateChecking = false;
let updateStarting = false;
let updatePollTimer: number | null = null;
let updateStatusRequest: Promise<void> | null = null;
let updateStatusSeq = 0;
let updateCheckSeq = 0;
let updateLifecycleSeq = 0;
let updateInitialized = false;
let updateCheckError: string | null = null;
let updateStatusError: string | null = null;
let updateStartError: string | null = null;

// Toast, Toggletip & Observer cleanup state
let toastTimer: number | null = null;
let popoverHintFallbackBound = false;
let interestPolyfillActiveTip: HTMLElement | null = null;
let interestCloseTimer: number | null = null;
let interestOpenTimer: number | null = null;
const tablistObservers = new Map<string, IntersectionObserver>();

// 后台调用可跳过或复用在途请求；强制刷新排在其后，并等待新请求真正结束。
function createRefreshQueue() {
  type Job = () => Promise<void>;
  let active: Promise<void> | null = null;
  let queued: { job: Job; result: Promise<void> } | null = null;
  function run(job: Job, whenBusy: "skip" | "join" | "queue" = "skip"): Promise<void> {
    if (active) {
      if (whenBusy === "skip") return Promise.resolve();
      if (whenBusy === "join") return active;
      if (queued) {
        queued.job = job;
        return queued.result;
      }
      const next = { job, result: Promise.resolve() };
      const flush = () => {
        if (queued !== next) return;
        queued = null;
        return run(next.job, "queue");
      };
      next.result = active.then(flush, flush);
      queued = next;
      return next.result;
    }
    active = Promise.resolve().then(job).finally(() => { active = null; });
    return active;
  }
  return { run, cancelPending: () => { queued = null; } };
}

function errorText(err: unknown): string {
  return (err instanceof Error ? err.message : String(err)) || "未知错误";
}

function canSendMessage(): boolean {
  const caps = app.getHostCapabilities() as Record<string, unknown> | undefined;
  return Boolean(caps && caps.message);
}

function cloneConfig(cfg: WorkbenchConfig): WorkbenchConfig {
  return JSON.parse(JSON.stringify(cfg)) as WorkbenchConfig;
}

function showToast(msg: string, actionLabel?: string, actionCb?: () => void): void {
  if (!resourcesActive) return;
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
    toastTimer = null;
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
  if (run.status === "running" && typeof run.timeout_sec === "number" && run.timeout_sec > 0) {
    if (typeof run.remaining_timeout_sec === "number") {
      localTimeoutDeadlineMs = Date.now() + Math.max(0, run.remaining_timeout_sec) * 1000;
    } else {
      localTimeoutDeadlineMs = localStartedMs + run.timeout_sec * 1000;
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
    const itemId = "item_id" in ev && typeof ev.item_id === "string" && ev.item_id ? ev.item_id : null;
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

// 每次替换监控目标都使上一代请求失效，即使后来又切回同一任务。
function resetMonitorSelection(runId: string | null, summary: RunSnapshot | null): number {
  const token = ++selectRunSeq;
  monitorSelectionPending = false;
  activeRunId = runId;
  currentRun = summary;
  actionFeedback = null;
  resetMonitorTimelineState();
  if (summary) updateLocalTiming(summary);
  return token;
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
    resourcesActive && !document.hidden && !monitorSelectionPending &&
    (viewMode === "inline" || (viewMode === "panel" && activeTab === "subagents")) &&
    Boolean(activeRunId) &&
    currentRun?.status === "running"
  );
}

function canPollMonitorList(): boolean {
  return resourcesActive && !document.hidden && viewMode === "panel" && activeTab === "subagents";
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
        if (ctxTimer) ctxTimer.textContent = `已运行 ${durText}`;
        const footerTimer = document.getElementById("running-footer-elapsed");
        if (footerTimer) footerTimer.textContent = `已运行 ${durText}`;
        const remEl = document.getElementById("timeout-remaining-hint");
        if (remEl && localTimeoutDeadlineMs !== null) {
          const remSec = Math.max(0, (localTimeoutDeadlineMs - Date.now()) / 1000);
          remEl.textContent = `超时剩余 ${formatDuration(remSec)}`;
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

function runAlertSignature(run: RunSnapshot | null): string {
  return JSON.stringify([run?.health, run?.health_reason, run?.in_flight_command, run?.recent_errors]);
}

function activeScopeParentSessionId(): string | undefined {
  return scopeMode === "session" && scopeSessionId ? scopeSessionId : undefined;
}

function getMonitorScopeInfo() {
  return {
    sessionId: scopeSessionId,
    sessionTitle: scopeSessionTitle,
    mode: scopeMode,
    otherSessionsRuns,
    totalUnclosedRuns,
  };
}

function computeMonitorSnapshotSig(): string {
  const runsSig = panelRuns
    .map((r) => {
      const seqOrBucket =
        typeof r.latest_seq === "number"
          ? `s${r.latest_seq}`
          : `e${Math.floor((r.duration_sec ?? r.elapsed_sec ?? 0) / 5)}`;
      return `${r.run_id}:${r.status}:${r.closed ? 1 : 0}:${r.tokens_used ?? 0}:${seqOrBucket}:${runAlertSignature(r)}`;
    })
    .join(",");
  return `${runsSig}|a=${activeRunsCount}|ga=${globalActiveRuns}|tu=${totalUnclosedRuns}|os=${otherSessionsRuns}|sm=${scopeMode}|sid=${scopeSessionId ?? ""}|c=${closed24hCount}|m=${maxConcurrentRuns}|en=${codexEnabled ? 1 : 0}|eo=${excludeOrchestratorModels ? 1 : 0}|rh=${retentionHours}|act=${activeRunId ?? ""}|cwd=${defaultCwd}`;
}

async function fetchIncrementalEvents(): Promise<void> {
  if (!activeRunId || pollInFlight || !canPollActiveRun()) return;
  const targetRunId = activeRunId;
  const pollToken = selectRunSeq;
  pollInFlight = true;
  try {
    const res = await app.callServerTool({
      name: "get_run_events",
      arguments: { run_id: targetRunId, since_seq: latestSeq },
    });
    const data = res?.structuredContent as RunSnapshot | undefined;
    if (pollToken !== selectRunSeq || activeRunId !== targetRunId || data?.run_id !== targetRunId) return;

    const idx = panelRuns.findIndex((r) => r.run_id === targetRunId);
    if (idx >= 0) {
      panelRuns[idx] = { ...panelRuns[idx], ...data };
    }
    const prevStatus = currentRun?.status;
    const prevTokens = currentRun?.tokens_used ?? null;
    const prevError = currentRun?.error ?? null;
    const prevAlerts = runAlertSignature(currentRun);
    const prevSeq = latestSeq;
    const incomingEvents = Array.isArray(data.events) ? data.events : [];

    currentRun = { ...currentRun, ...data };
    updateLocalTiming(data);
    if (incomingEvents.length > 0) applyIncomingEvents(incomingEvents);
    if (typeof data.latest_seq === "number") latestSeq = Math.max(latestSeq, data.latest_seq);

    const changed =
      incomingEvents.length > 0 ||
      data.status !== prevStatus ||
      (data.tokens_used ?? null) !== prevTokens ||
      (data.error ?? null) !== prevError ||
      runAlertSignature(data) !== prevAlerts ||
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

function applyRunListMetadata(data: ListRunsPayload | CloseRunPayload, configReadSeq?: number): void {
  if (Array.isArray(data.runs)) {
    panelRuns = data.runs;
  }
  if (typeof data.active_runs === "number") {
    activeRunsCount = data.active_runs;
  } else if (Array.isArray(data.runs)) {
    activeRunsCount = data.runs.filter((r) => r.status === "running").length;
  }
  if (typeof data.global_active_runs === "number") {
    globalActiveRuns = data.global_active_runs;
  } else if (scopeMode === "all") {
    globalActiveRuns = activeRunsCount;
  }
  if (typeof data.total_unclosed_runs === "number") {
    totalUnclosedRuns = data.total_unclosed_runs;
  } else if (scopeMode === "all") {
    totalUnclosedRuns = panelRuns.length;
  }
  if (typeof data.other_sessions_runs === "number") {
    otherSessionsRuns = data.other_sessions_runs;
  } else if (scopeMode === "all") {
    otherSessionsRuns = 0;
  }
  if (typeof data.closed_24h_count === "number") {
    closed24hCount = data.closed_24h_count;
  }
  if ("max_concurrent_runs" in data && typeof data.max_concurrent_runs === "number") {
    maxConcurrentRuns = data.max_concurrent_runs;
  }
  if (configReadSeq === configMutationSeq && !configWritePending) {
    applyConfigFlags(data as Partial<ConfigFlags>);
  }
  if ("default_cwd" in data && data.default_cwd?.trim()) defaultCwd = data.default_cwd.trim();
  if ("retention_hours" in data && typeof data.retention_hours === "number") {
    retentionHours = data.retention_hours;
  }
}

function refreshMonitorRuns(force = false): Promise<void> {
  return monitorRefresh.run(() => loadMonitorRuns(force), force ? "queue" : "skip");
}

async function loadMonitorRuns(force: boolean): Promise<void> {
  if (!resourcesActive) return;
  listInFlight = true;
  const configReadSeq = configMutationSeq;
  try {
    const prevSig = computeMonitorSnapshotSig();
    const prevActiveId = activeRunId;
    const listReadSeq = monitorRunMutationSeq;
    const parentSid = activeScopeParentSessionId();
    const res = await app.callServerTool({
      name: "list_runs",
      arguments: {
        status: "all",
        limit: 40,
        include_closed: false,
        ...(parentSid ? { parent_session_id: parentSid } : {}),
      },
    });
    const data = res?.structuredContent as ListRunsPayload | undefined;
    if (!resourcesActive || viewMode !== "panel") return;
    if (res?.isError || !Array.isArray(data?.runs)) {
      throw new Error(extractToolErrorMessage(res, "任务列表接口返回空数据"));
    }
    // 新建或追问已返回更新的列表时，不让旧的列表请求覆盖它。
    if (listReadSeq === monitorRunMutationSeq) applyRunListMetadata(data, configReadSeq);

    let focusedChanged = false;
    if (panelRuns.length === 0) {
      if (activeRunId !== null || currentRun !== null || monitorTimeline.length > 0) {
        focusedChanged = true;
      }
      if (focusedChanged) resetMonitorSelection(null, null);
      narrowPickerOpen = false;
    } else if (!activeRunId || !panelRuns.some((r) => r.run_id === activeRunId)) {
      if (force || activeTab === "subagents") {
        await selectRun(panelRuns[0].run_id, force);
        return;
      }
      resetMonitorSelection(panelRuns[0].run_id, panelRuns[0]);
      focusedChanged = true;
    } else if (force) {
      await selectRun(activeRunId, true);
      return;
    } else if (currentRun && currentRun.run_id === activeRunId) {
      const fresh = panelRuns.find((r) => r.run_id === activeRunId);
      if (
        fresh &&
        !monitorSelectionPending &&
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
    if ((force || sigChanged) && viewMode === "panel" && activeTab === "subagents") {
      render();
    }
  } catch (err) {
    if (force) throw err;
  } finally {
    listInFlight = false;
    syncTickTimer();
    scheduleNextEventPoll();
    scheduleNextMonitorListRefresh();
  }
}

async function selectRun(runId: string, force = false): Promise<void> {
  if (!resourcesActive) return;
  const listRun = panelRuns.find((r) => r.run_id === runId) ?? null;
  const keepVisibleTrace = force && activeRunId === runId && currentRun?.run_id === runId;
  const token = keepVisibleTrace ? ++selectRunSeq : resetMonitorSelection(runId, listRun);
  monitorSelectionPending = true;
  syncPollingTimers();
  if (viewMode === "inline" || activeTab === "subagents") render();
  try {
    const res = await app.callServerTool({
      name: "get_run_events",
      arguments: { run_id: runId, since_seq: 0 },
    });
    if (force && (res?.isError || (res?.structuredContent as RunSnapshot | undefined)?.run_id !== runId)) {
      throw new Error(extractToolErrorMessage(res, "任务事件接口返回空数据"));
    }
    if (token !== selectRunSeq || activeRunId !== runId) return;
    const data = res?.structuredContent as RunSnapshot | undefined;
    if (!res?.isError && data?.run_id === runId) {
      resetRunTimeline({ ...(keepVisibleTrace ? currentRun : listRun), ...data });
      const idx = panelRuns.findIndex((r) => r.run_id === runId);
      if (idx >= 0) panelRuns[idx] = { ...panelRuns[idx], ...data };
    }
  } catch (err) {
    if (force) throw err;
    // 保留列表摘要；下一次轮询从 0 重新取日志。
  } finally {
    if (token === selectRunSeq && activeRunId === runId) {
      monitorSelectionPending = false;
      syncPollingTimers();
      if (viewMode === "inline" || activeTab === "subagents") render();
    }
  }
}

function applyArchiveResult(data: CloseRunPayload, closedIds: string[]): void {
  const closed = new Set(closedIds);
  applyRunListMetadata({ ...data, runs: (data.runs ?? panelRuns).filter((run) => !closed.has(run.run_id)) });
  if (typeof data.closed_24h_count !== "number") closed24hCount += closed.size;
  newArchiveBadge = true;
  if (panelRuns.length === 0) narrowPickerOpen = false;
}

async function handleCloseRun(runId: string): Promise<void> {
  const closingRun = panelRuns.find((r) => r.run_id === runId);
  const parentSid = activeScopeParentSessionId();
  try {
    const res = await app.callServerTool({
      name: "close_run",
      arguments: { run_id: runId, ...(parentSid ? { parent_session_id: parentSid } : {}) },
    });
    const data = res?.structuredContent as CloseRunPayload | undefined;
    if (res?.isError || !Array.isArray(data?.closed_ids) || !data.closed_ids.includes(runId)) {
      throw new Error(extractToolErrorMessage(res, "服务端未确认此任务已归档"));
    }
    applyArchiveResult(data, data.closed_ids);
    if (activeRunId === runId) {
      if (panelRuns.length > 0) {
        await selectRun(panelRuns[0].run_id);
      } else {
        resetMonitorSelection(null, null);
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
  const parentSid = activeScopeParentSessionId();
  try {
    const res = await app.callServerTool({
      name: "close_run",
      arguments: { close_all_finished: true, ...(parentSid ? { parent_session_id: parentSid } : {}) },
    });
    const data = res?.structuredContent as CloseRunPayload | undefined;
    if (res?.isError || !Array.isArray(data?.closed_ids) || data.closed_ids.length === 0) {
      throw new Error(extractToolErrorMessage(res, "服务端未确认任何任务已归档"));
    }
    applyArchiveResult(data, data.closed_ids);
    const closedCount = data.closed_ids.length;
    if (!panelRuns.some((r) => r.run_id === activeRunId)) {
      if (panelRuns.length > 0) {
        await selectRun(panelRuns[0].run_id);
      } else {
        resetMonitorSelection(null, null);
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

type ConfigFlags = Pick<WorkbenchConfig, "enabled" | "intercept_native_subagents" | "exclude_orchestrator_models">;

function currentConfigFlags(): Required<ConfigFlags> {
  return { enabled: codexEnabled, intercept_native_subagents: interceptNativeSubagents, exclude_orchestrator_models: excludeOrchestratorModels };
}

function applyConfigFlags(flags: ConfigFlags): void {
  codexEnabled = flags.enabled ?? codexEnabled;
  interceptNativeSubagents = flags.intercept_native_subagents ?? interceptNativeSubagents;
  excludeOrchestratorModels = flags.exclude_orchestrator_models ?? excludeOrchestratorModels;
  if (workbenchDraft) Object.assign(workbenchDraft, currentConfigFlags());
}

function beginConfigWrite(): boolean {
  if (!resourcesActive || configWritePending) return false;
  configMutationSeq++;
  configWritePending = true;
  if (activeTab === "models") syncDraftFromDom();
  return true;
}

function endConfigWrite(): void {
  configWritePending = false;
  // 写入过程中启动的读取也可能观察到旧数据。
  configMutationSeq++;
  render();
}

async function toggleConfigFlag(
  key: keyof ConfigFlags,
  nextVal: boolean,
  tool: "toggle_codex_enabled" | "toggle_exclude_orchestrator" | "toggle_intercept_native_subagents",
  successMessages: [string, string],
): Promise<void> {
  if (!beginConfigWrite()) return;
  const previous = currentConfigFlags()[key];
  applyConfigFlags({ [key]: nextVal });
  render();
  try {
    const res = await app.callServerTool({ name: tool, arguments: { enabled: nextVal } });
    const data = res?.structuredContent as (ConfigFlags & { saved?: WorkbenchConfig }) | undefined;
    if (res?.isError || !data) throw new Error(extractToolErrorMessage(res, "开关服务未返回配置"));
    applyConfigFlags({ [key]: data[key] ?? data.saved?.[key] ?? data.enabled ?? nextVal });
    showToast(successMessages[currentConfigFlags()[key] ? 0 : 1]);
  } catch (err) {
    applyConfigFlags({ [key]: previous });
    showToast(`切换失败: ${String(err)}`);
  } finally {
    if (activeTab === "models") syncDraftFromDom();
    endConfigWrite();
  }
}

function populateHistoryTimelineFromRun(run: RunSnapshot | null): void {
  historyTimelineRunId = run?.run_id ?? null;
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
  const r = res as { structuredContent?: { error?: unknown }; content?: Array<{ text?: string }> } | undefined;
  const detail = r?.structuredContent?.error;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  return r?.content?.find((item) => typeof item.text === "string" && item.text.trim())?.text?.trim() || fallback;
}

function refreshHistory(focusRunId?: string, queueIfBusy = false): Promise<void> {
  return historyRefresh.run(() => loadHistory(focusRunId), queueIfBusy ? "queue" : "skip");
}

async function loadHistory(focusRunId?: string): Promise<void> {
  if (!resourcesActive) return;
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
    historyLoading = false;
    if (activeTab === "history" && viewMode === "panel") {
      render();
    }
  }
}

async function loadHistoryRunDetail(runId: string): Promise<void> {
  const detailToken = ++historyDetailSeq;
  historySelectedRunId = runId;
  const flatRuns = (historyPayload?.sessions || []).flatMap((s) => s.runs || []);
  const summaryRun = flatRuns.find((r) => r.run_id === runId) || null;
  if (historyTimelineRunId !== runId) {
    historySelectedRunDetail = summaryRun;
    populateHistoryTimelineFromRun(null);
  }
  if (summaryRun?.expired) {
    historySelectedRunDetail = summaryRun;
    populateHistoryTimelineFromRun(null);
    if (activeTab === "history" && viewMode === "panel") {
      render();
    }
    return;
  }
  if (activeTab === "history" && viewMode === "panel") render();
  try {
    const res = await app.callServerTool({
      name: "get_run_events",
      arguments: { run_id: runId, since_seq: 0 },
    });
    if (detailToken !== historyDetailSeq || historySelectedRunId !== runId) return;
    const detail = res?.structuredContent as RunSnapshot | undefined;
    if (detail?.run_id === runId) {
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

function refreshStats(queueIfBusy = false): Promise<void> {
  return statsRefresh.run(loadStats, queueIfBusy ? "queue" : "skip");
}

async function loadStats(): Promise<void> {
  if (!resourcesActive) return;
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
    statsLoading = false;
    if (activeTab === "stats" && viewMode === "panel") {
      render();
    }
  }
}

function refreshWorkbenchConfig(keepDraft = false): Promise<void> {
  return configRefresh.run(() => loadWorkbenchConfig(keepDraft), keepDraft ? "queue" : "join");
}

async function loadWorkbenchConfig(keepDraft: boolean): Promise<void> {
  if (!resourcesActive) return;
  const configReadSeq = configMutationSeq;
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
      if (data.default_cwd?.trim()) defaultCwd = data.default_cwd.trim();
      const freshConfig = configReadSeq === configMutationSeq && !configWritePending;
      if (freshConfig || !workbenchData) workbenchData = data;
      if (freshConfig) {
        maxConcurrentRuns = data.max_concurrent_runs || maxConcurrentRuns;
        if (typeof data.retention_hours === "number") retentionHours = data.retention_hours;
      }
      if (!workbenchDraft || (freshConfig && !keepDraft)) {
        workbenchDraft = cloneConfig(data.workbench_config);
      } else if (activeTab === "models") {
        syncDraftFromDom();
      }
      applyConfigFlags(freshConfig ? data : {});
    }
  } catch (err) {
    modelsLoadError = err instanceof Error ? err.message : String(err);
  } finally {
    modelsLoading = false;
    if (activeTab === "models" && viewMode === "panel") {
      render();
    }
  }
}

function paintManualRefreshButton(): void {
  const button = document.querySelector<HTMLButtonElement>("#panel-refresh-btn");
  if (!button) return;
  button.disabled = manualRefreshing;
  button.classList.toggle("is-spinning", manualRefreshing);
  button.setAttribute("aria-busy", String(manualRefreshing));
}

async function handleManualRefresh(): Promise<void> {
  if (manualRefreshing || !resourcesActive) return;
  manualRefreshing = true;
  paintManualRefreshButton();
  const minFeedback = new Promise<void>((resolve) => window.setTimeout(resolve, 260));
  const tab = activeTab;
  const failurePrefix = { subagents: "刷新活跃监控失败", history: "刷新历史对话失败", stats: "刷新统计面板失败", models: "刷新配置失败" }[tab];
  let message: string;
  try {
    if (tab === "subagents") {
      await refreshMonitorRuns(true);
      message = `已刷新活跃监控 (${panelRuns.length} 个任务 | ${activeRunsCount} 运行中)`;
    } else if (tab === "history") {
      await refreshHistory(undefined, true);
      if (historyLoadError) throw new Error(historyLoadError);
      message = `已刷新历史对话 (${historyPayload?.total_runs ?? 0} 条记录)`;
    } else if (tab === "stats") {
      await refreshStats(true);
      if (statsLoadError) throw new Error(statsLoadError);
      message = `已刷新统计面板 (${statsPayload?.range ?? statsRange})`;
    } else {
      const [configResult, statusResult] = await Promise.allSettled([refreshWorkbenchConfig(true), refreshUpdateStatus()]);
      if (modelsLoadError) throw new Error(modelsLoadError);
      if (configResult.status === "rejected") throw configResult.reason;
      if (statusResult.status === "rejected") throw statusResult.reason;
      message = `已刷新模型与配置 (${workbenchData?.visible_models_count ?? 0} 个可用模型)`;
    }
  } catch (err) {
    message = `${failurePrefix}: ${errorText(err)}`;
  } finally {
    await minFeedback;
    manualRefreshing = false;
    paintManualRefreshButton();
  }
  showToast(message);
}

function blankUserTaskDraft(): UserTaskDraft {
  return { prompt: "", cwd: "", model: "", effort: "", mode: "write", worktree: "", persist: true };
}

function getComposerDraft(): UserTaskDraft {
  return composerCwdEdited ? composerDraft : { ...composerDraft, cwd: defaultCwd || panelRuns[0]?.cwd || "" };
}

function getComposerModels() {
  return (workbenchData?.models ?? []).filter((model) => !model.excluded && !model.orchestrator_excluded);
}

function openComposer(trigger: HTMLElement): void {
  if (composerOpen) {
    document.getElementById("composer-prompt")?.focus();
    return;
  }
  narrowPickerOpen = false;
  composerOpen = true;
  composerTriggerId = trigger.id;
  render();
  document.getElementById("composer-prompt")?.focus();
  if (!workbenchData) {
    void refreshWorkbenchConfig().then(() => {
      if (composerOpen && viewMode === "panel" && activeTab === "subagents") render();
    });
  }
}

function closeComposer(): void {
  if (!composerOpen || composerSending) return;
  composerOpen = false;
  composerError = null;
  render();
  const trigger = document.getElementById(composerTriggerId);
  const visibleTrigger = trigger?.getClientRects().length ? trigger :
    Array.from(document.querySelectorAll<HTMLElement>("[data-open-composer]")).find((button) => button.getClientRects().length);
  visibleTrigger?.focus({ preventScroll: true });
}

async function submitUserTask(): Promise<void> {
  if (composerSending || !composerOpen) return;
  const draft = getComposerDraft();
  const prompt = draft.prompt.trim();
  const cwd = draft.cwd.trim();
  const normalizedCwd = cwd.replace(/\\/g, "/");
  const isAbsolute = cwd.startsWith("/") || /^[A-Za-z]:[/]/.test(normalizedCwd) || /^[/]{2}[^/]+[/][^/]+/.test(normalizedCwd);
  const unavailableModel = Boolean(draft.model) && !getComposerModels().some((model) => model.slug === draft.model);
  if (!prompt || !isAbsolute || unavailableModel) {
    composerError = !prompt ? "请输入任务指令。" : !isAbsolute ? "请输入有效的工作目录绝对路径。" : "当前模型已不可用，请重新选择。";
    render();
    document.getElementById(!prompt ? "composer-prompt" : !isAbsolute ? "composer-cwd" : "composer-model")?.focus();
    return;
  }

  composerSending = true;
  composerError = null;
  render();
  let started = false;
  try {
    const parentSid = activeScopeParentSessionId();
    const res = await app.callServerTool({
      name: "start_user_task",
      arguments: {
        prompt, cwd, mode: draft.mode, persist: draft.persist, raw: true,
        ...(parentSid ? { conversation_id: parentSid, ...(scopeSessionTitle ? { conversation_title: scopeSessionTitle } : {}) } : {}),
        ...(draft.model ? { model: draft.model } : {}),
        ...(draft.effort ? { effort: draft.effort } : {}),
        ...(draft.worktree.trim() ? { worktree: draft.worktree.trim() } : {}),
      },
    });
    const data = res?.structuredContent as StartUserTaskPayload | undefined;
    if (res?.isError || !data?.run_id) throw new Error(extractToolErrorMessage(res, "服务端未返回新任务"));
    if (!resourcesActive || viewMode !== "panel") return;
    const {
      active_runs,
      max_concurrent_runs,
      runs,
      global_active_runs,
      total_unclosed_runs,
      other_sessions_runs,
      scope_session_id: _scopeSid,
      ...newRun
    } = data;
    composerOpen = false;
    composerCwdEdited = false;
    composerDraft = blankUserTaskDraft();
    activeTab = "subagents";
    monitorRunMutationSeq++;
    panelRuns = Array.isArray(runs) && runs.some((run) => run.run_id === newRun.run_id)
      ? runs.map((run) => run.run_id === newRun.run_id ? { ...run, ...newRun } : run)
      : [newRun, ...(runs ?? panelRuns).filter((run) => run.run_id !== newRun.run_id)];
    activeRunsCount = active_runs;
    if (typeof global_active_runs === "number") globalActiveRuns = global_active_runs;
    else globalActiveRuns = Math.max(globalActiveRuns, active_runs);
    if (typeof total_unclosed_runs === "number") totalUnclosedRuns = total_unclosed_runs;
    if (typeof other_sessions_runs === "number") otherSessionsRuns = other_sessions_runs;
    maxConcurrentRuns = max_concurrent_runs;
    resetMonitorSelection(newRun.run_id, newRun);
    resetRunTimeline(newRun);
    syncPollingTimers();
    started = true;
  } catch (err) {
    composerError = errorText(err);
  } finally {
    composerSending = false;
    render();
    if (started) showToast("已发起 Codex 直连会话");
  }
}

function bindComposer(): void {
  document.querySelectorAll<HTMLElement>("[data-open-composer]").forEach((trigger) => {
    trigger.setAttribute("aria-expanded", String(composerOpen));
    if (composerOpen) trigger.setAttribute("aria-controls", "user-task-drawer");
    trigger.addEventListener("click", () => openComposer(trigger));
  });
  const form = document.querySelector<HTMLFormElement>("#user-task-form");
  if (!form) return;
  const read = <T extends HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(id: string) => form.querySelector<T>(`#composer-${id}`)!;
  const prompt = read<HTMLTextAreaElement>("prompt");
  const cwd = read<HTMLInputElement>("cwd");
  for (const eventType of ["input", "change"]) {
    form.addEventListener(eventType, (event) => {
      if (event.target === cwd) composerCwdEdited = true;
      composerDraft = {
        prompt: prompt.value, cwd: cwd.value, model: read("model").value,
        effort: read("effort").value, worktree: read("worktree").value,
        mode: read("mode").value === "read-only" ? "read-only" : "write",
        persist: read<HTMLInputElement>("persist").checked,
      };
    });
  }
  prompt.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.isComposing) {
      event.preventDefault();
      void submitUserTask();
    }
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitUserTask();
  });
  document.getElementById("cancel-user-task-btn")?.addEventListener("click", closeComposer);
}

type UpdateCheckArgs = {
  force?: boolean;
  channel?: "auto" | "release" | "main";
  dismiss_version?: string;
};

function getUpdateViewState() {
  return {
    check: updateCheck,
    status: updateStatus,
    pluginVersion: workbenchData?.plugin_version,
    checking: updateChecking,
    starting: updateStarting,
    error: updateStartError || updateCheckError || updateStatusError,
  };
}

async function checkForUpdates(args: UpdateCheckArgs, announce = false): Promise<void> {
  if (!resourcesActive || viewMode !== "panel") return;
  const seq = ++updateCheckSeq;
  updateChecking = true;
  updateCheckError = null;
  renderUpdateUi();
  try {
    const res = await app.callServerTool({ name: "check_for_updates", arguments: args });
    if (seq !== updateCheckSeq || !resourcesActive || viewMode !== "panel") return;
    const data = res?.structuredContent as UpdateCheckPayload | undefined;
    if (res?.isError || !data?.ok) throw new Error(data?.error || extractToolErrorMessage(res, "更新检查未返回有效结果"));
    updateCheck = data;
    if (announce) {
      const label = remoteUpdateLabel(data);
      showToast(args.dismiss_version ? "已忽略此版本" : data.update_available
        ? `发现更新${label ? `: ${label}` : ""}` : "当前已是最新版本");
    }
  } catch (err) {
    if (seq !== updateCheckSeq || !resourcesActive || viewMode !== "panel") return;
    updateCheckError = `${args.dismiss_version ? "忽略版本失败" : "检查更新失败"}: ${errorText(err)}`;
    if (announce) showToast(updateCheckError);
  } finally {
    if (seq === updateCheckSeq) {
      updateChecking = false;
      renderUpdateUi();
    }
  }
}

function stopUpdatePolling(): void {
  if (updatePollTimer !== null) window.clearTimeout(updatePollTimer);
  updatePollTimer = null;
}

function scheduleUpdatePolling(): void {
  if (!resourcesActive || viewMode !== "panel" || document.hidden || !isUpdateRunning(updateStatus)) {
    stopUpdatePolling();
    return;
  }
  if (updatePollTimer !== null || updateStarting || updateStatusRequest) return;
  updatePollTimer = window.setTimeout(() => {
    updatePollTimer = null;
    if (!resourcesActive || viewMode !== "panel" || document.hidden || !isUpdateRunning(updateStatus)) return;
    void refreshUpdateStatus().catch(() => { /* 状态卡显示错误，下一次轮询重试。 */ });
  }, 1500);
}

function applyUpdateStatus(status: UpdateStatusPayload, fromStart = false): void {
  const wasRunning = isUpdateRunning(updateStatus);
  updateStatus = status;
  if (status.check?.ok && (fromStart || !updateCheck)) updateCheck = status.check;
  if (!isUpdateRunning(status)) stopUpdatePolling();
  if ((wasRunning || fromStart) && (status.status === "completed" || status.status === "failed")) {
    showToast(status.status === "completed"
      ? `插件更新完成。${MCP_REFRESH_GUIDANCE}`
      : `插件更新失败: ${status.error || status.message || status.phase_label}`);
    void checkForUpdates({ force: true, channel: status.channel ?? updateCheck?.channel ?? "auto" });
  }
  renderUpdateUi();
}

function refreshUpdateStatus(): Promise<void> {
  if (!resourcesActive || viewMode !== "panel") return Promise.resolve();
  if (updateStatusRequest) return updateStatusRequest;
  const seq = updateStatusSeq;
  const request = Promise.resolve().then(async () => {
    const res = await app.callServerTool({ name: "get_update_status", arguments: {} });
    const data = res?.structuredContent as UpdateStatusPayload | undefined;
    if (res?.isError || !data?.status) throw new Error(extractToolErrorMessage(res, "更新状态接口返回空数据"));
    if (seq !== updateStatusSeq || !resourcesActive || viewMode !== "panel" || updateStarting) return;
    updateStatusError = null;
    applyUpdateStatus(data);
  }).catch((err) => {
    if (seq === updateStatusSeq && resourcesActive && viewMode === "panel" && !updateStarting) {
      updateStatusError = `获取更新状态失败: ${errorText(err)}`;
      renderUpdateUi();
    }
    throw err;
  }).finally(() => {
    if (updateStatusRequest === request) updateStatusRequest = null;
    scheduleUpdatePolling();
  });
  updateStatusRequest = request;
  return request;
}

async function startPluginUpdate(): Promise<void> {
  if (!resourcesActive || updateStarting || updateChecking || isUpdateRunning(updateStatus) || updateBlockReason(getUpdateViewState())) return;
  const lifecycle = updateLifecycleSeq;
  updateStarting = true;
  updateStatusSeq++;
  stopUpdatePolling();
  updateStartError = updateStatusError = null;
  renderUpdateUi();
  try {
    const channel = updateCheck?.channel ?? "auto";
    const expectedVersion = updateCheck?.latest_id || updateCheck?.remote_version;
    const res = await app.callServerTool({
      name: "start_plugin_update",
      arguments: { channel, ...(expectedVersion ? { expected_version: expectedVersion } : {}) },
    });
    if (lifecycle !== updateLifecycleSeq || !resourcesActive || viewMode !== "panel") return;
    const data = res?.structuredContent as UpdateStatusPayload | undefined;
    if (res?.isError || !data?.status) throw new Error(data?.error || extractToolErrorMessage(res, "服务端未返回更新阶段"));
    applyUpdateStatus(data, true);
  } catch (err) {
    if (lifecycle !== updateLifecycleSeq || !resourcesActive || viewMode !== "panel") return;
    updateStartError = `启动更新失败: ${errorText(err)}`;
    showToast(updateStartError);
  } finally {
    if (lifecycle === updateLifecycleSeq) {
      updateStarting = false;
      updateStatusSeq++;
      renderUpdateUi();
      scheduleUpdatePolling();
    }
  }
}

function hasUpdateNotice(): boolean {
  return Boolean(updateCheck?.update_available && !updateCheck.is_dismissed && updateStatus?.status !== "completed");
}

function renderUpdateBanner(): string {
  if (!hasUpdateNotice() || !updateCheck) return "";
  const label = remoteUpdateLabel(updateCheck);
  const blocked = updateBlockReason(getUpdateViewState());
  const busy = updateChecking || updateStarting || isUpdateRunning(updateStatus);
  return `<div class="update-banner">
    <span>发现新版本${label ? ` ${escapeHtml(label)}` : ""}</span>
    <div class="update-banner-actions">
      <button type="button" id="banner-update-now-btn" class="btn btn-primary btn-sm"${busy || blocked ? " disabled" : ""}${blocked ? ` title="${escapeHtml(blocked)}"` : ""}>立即更新</button>
      <button type="button" id="banner-update-detail-btn" class="btn btn-sm">查看详情</button>
      <button type="button" id="banner-update-dismiss-btn" class="link-btn"${!updateCheck.latest_id || busy ? " disabled" : ""}>忽略</button>
    </div>
  </div>`;
}

function bindUpdateActions(container: ParentNode): void {
  const repoLink = container.querySelector<HTMLAnchorElement>("#plugin-update-repo-link");
  repoLink?.addEventListener("click", (event) => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    if (!app.getHostCapabilities()?.openLinks) return;
    event.preventDefault();
    void app.openLink({ url: repoLink.href }).then((result) => {
      if (result?.isError) showToast("主机未能打开 GitHub 仓库");
    }).catch((err) => showToast(`打开 GitHub 仓库失败: ${errorText(err)}`));
  });
  container.querySelector("#check-updates-btn")?.addEventListener("click", () => {
    void checkForUpdates({ force: true, channel: "auto" }, true);
  });
  container.querySelector("#check-main-updates-btn")?.addEventListener("click", () => {
    void checkForUpdates({ force: true, channel: "main" }, true);
  });
  for (const button of container.querySelectorAll("#start-update-btn, #banner-update-now-btn")) {
    button.addEventListener("click", () => void startPluginUpdate());
  }
  container.querySelector("#banner-update-detail-btn")?.addEventListener("click", () => {
    if (activeTab !== "models") switchTab("models");
    document.getElementById("plugin-update-card")?.scrollIntoView({ block: "nearest" });
  });
  container.querySelector("#banner-update-dismiss-btn")?.addEventListener("click", () => {
    const latestId = updateCheck?.latest_id;
    if (latestId) void checkForUpdates({ dismiss_version: latestId }, true);
  });
}

const updateMarkup = new WeakMap<Element, string>();

function renderUpdateUi(): void {
  if (!resourcesActive || viewMode !== "panel") return;
  // 仅替换更新区域，不打断正在编辑的配置或对话草稿。
  const focused = document.activeElement as HTMLElement | null;
  const notice = hasUpdateNotice();
  document.querySelectorAll<HTMLElement>(".badge-dot.is-update").forEach((dot) => { dot.hidden = !notice; });
  const bannerRegion = document.getElementById("panel-update-region");
  if (bannerRegion) {
    const html = renderUpdateBanner();
    if (html !== updateMarkup.get(bannerRegion)) {
      bannerRegion.innerHTML = html;
      updateMarkup.set(bannerRegion, html);
      bindUpdateActions(bannerRegion);
    }
  }
  const card = document.getElementById("plugin-update-card");
  if (card) {
    const html = renderUpdateCard(getUpdateViewState());
    if (html !== updateMarkup.get(card)) {
      const previousNotes = card.querySelector<HTMLDetailsElement>("#plugin-update-notes");
      const notesOpen = Boolean(previousNotes?.open);
      const notesScrollTop = previousNotes?.querySelector("div")?.scrollTop ?? 0;
      card.outerHTML = html;
      const newCard = document.getElementById("plugin-update-card");
      if (newCard) {
        updateMarkup.set(newCard, html);
        bindUpdateActions(newCard);
        const notes = newCard.querySelector<HTMLDetailsElement>("#plugin-update-notes");
        if (notes && notesOpen) {
          notes.open = true;
          const body = notes.querySelector("div");
          if (body) body.scrollTop = notesScrollTop;
        }
      }
    }
  }
  if (focused?.id && !focused.isConnected) document.getElementById(focused.id)?.focus({ preventScroll: true });
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
    const timeout = (id: string) => Math.min(86400, Math.max(0, Number.parseInt(val(id), 10) || 0));
    workbenchDraft.mapping.worker_timeout_sec = timeout("cfg-map-worker-timeout");
    workbenchDraft.mapping.idle_timeout_sec = timeout("cfg-map-idle-timeout");
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
    // Antigravity 的宿主扩展不在 SDK 的标准请求联合类型内。
    const request = { method: "com.google.antigravity/toggle-aux-pane", params: { tabType: "fileView", uri } };
    await app.request(request as unknown as Parameters<typeof app.request>[0], AuxPaneResponseSchema);
  } catch {
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(filePath).catch(() => {});
      showToast(`已复制路径: ${filePath}`);
    }
  }
}

function switchTab(tab: PanelTab, focusHistoryRunId?: string): void {
  const wasMonitoring = activeTab === "subagents";
  activeTab = tab;
  if (tab !== "subagents") narrowPickerOpen = false;
  if (tab === "history") {
    newArchiveBadge = false;
    historyInspecting = Boolean(focusHistoryRunId);
  }
  if (tab === "subagents" && !wasMonitoring && activeRunId) {
    void selectRun(activeRunId);
    void refreshMonitorRuns();
    return;
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
    timelineRunId: activeRunId,
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
    timelineRunId: historyTimelineRunId,
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
      if (!resourcesActive) return;
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
      if (!resourcesActive) return;
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

function setNarrowPickerOpen(open: boolean, restoreFocus = false): void {
  const picker = document.getElementById("narrow-agent-picker-btn");
  const popover = document.getElementById("narrow-agent-popover");
  if (!picker || !popover) return;
  narrowPickerOpen = open;
  picker.setAttribute("aria-expanded", String(open));
  popover.hidden = !open;
  if (open) {
    popover.querySelector<HTMLElement>('[role="option"][aria-selected="true"]')?.focus();
  } else if (restoreFocus) {
    picker.focus();
  }
}

function render(): void {
  if (!resourcesActive) return;
  const root = document.getElementById("root");
  if (!root) return;

  const wasFocused = document.activeElement as HTMLElement | null;
  const composerBox = document.getElementById("user-task-drawer");
  const composerScrollTop = composerBox?.scrollTop ?? 0;
  const composerFocus = wasFocused && composerBox?.contains(wasFocused) ? wasFocused.id : "";
  const composerSelection = composerFocus && (wasFocused instanceof HTMLInputElement || wasFocused instanceof HTMLTextAreaElement)
    ? [wasFocused.selectionStart, wasFocused.selectionEnd] : null;
  const switcherFocusId = wasFocused?.closest(".narrow-agent-switcher-bar") ? wasFocused.id : "";
  const wideTabFocusId = wasFocused?.closest("#wide-agent-tablist") ? wasFocused.id : "";
  const pickerScroll = document.getElementById("narrow-agent-popover")?.scrollTop ?? 0;
  const existingBox = document.getElementById("timeline-box");
  const trace = existingBox?.closest<HTMLElement>(".trace-shell");
  if (existingBox && trace) {
    if (trace.dataset.traceMode === "history" && trace.dataset.traceRun === historySelectedRunId) {
      historySavedScrollTop = existingBox.scrollTop;
    } else if (trace.dataset.traceMode === "monitor" && trace.dataset.traceRun === activeRunId) {
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
    history: "历史对话",
    stats: "统计面板",
    models: "模型与配置",
  };

  const wideTablistHtml = renderMonitorTablist(panelRuns, activeRunId, "wide", retentionHours);

  const scopeInfo = getMonitorScopeInfo();
  const narrowScopePillHtml = isMonitor && scopeSessionId
    ? `<button type="button" id="narrow-scope-toggle" data-toggle-scope class="scope-pill-btn${scopeMode === "session" ? " is-scoped" : ""}" title="${escapeHtml(
        scopeMode === "session"
          ? `当前已按本对话隔离显示${otherSessionsRuns > 0 ? `；其他并行对话另有 ${otherSessionsRuns} 个活跃任务` : ""}，点击切换查看全部`
          : "当前显示全部并行对话的活跃任务，点击切回仅看当前对话",
      )}">${scopeMode === "session" ? "本对话" : "全部"}${scopeMode === "session" && otherSessionsRuns > 0 ? `<span class="scope-other-badge tabular-nums">+${otherSessionsRuns}</span>` : ""}</button>`
    : "";
  const wideScopePillHtml = scopeSessionId
    ? `<button type="button" id="wide-scope-toggle" data-toggle-scope class="scope-pill-btn${scopeMode === "session" ? " is-scoped" : ""}" title="${escapeHtml(
        scopeMode === "session"
          ? "当前已按本对话隔离，点击查看全部并行对话"
          : "当前显示全部对话，点击切回仅看本对话",
      )}">${scopeMode === "session"
        ? `仅本对话${otherSessionsRuns > 0 ? ` (+${otherSessionsRuns} 其他)` : ""}`
        : `全部对话 (${totalUnclosedRuns || panelRuns.length})`}</button>`
    : "";
  const slotTooltip = scopeSessionId && scopeMode === "session"
    ? `全局并发槽位 ${globalActiveRuns}/${maxConcurrentRuns} | 当前对话 ${activeRunsCount} 运行中`
    : `当前并发槽位使用情况 ${globalActiveRuns}/${maxConcurrentRuns}`;

  const updateDot = `<span class="badge-dot is-update" role="img" aria-label="有插件更新" title="有插件更新"${hasUpdateNotice() ? "" : " hidden"}></span>`;
  const topbarHtml = `<div class="topbar">
    <div class="narrow-subpage-bar">
      ${isMonitor
        ? `<span class="narrow-monitor-title">${subTitleMap.subagents}</span><span class="tab-count-pill tabular-nums" title="当前视图任务数量">${panelRuns.length}</span>${narrowScopePillHtml}`
        : `<button type="button" id="narrow-back-btn" class="btn btn-sm">← 返回监控</button><span class="narrow-subpage-title">${subTitleMap[activeTab]}</span>`}
    </div>

    <div class="wide-only-nav wide-top-nav" role="tablist" aria-label="控制中心主视图">
      <button type="button" role="tab" data-nav-tab="subagents" aria-selected="${activeTab === "subagents" ? "true" : "false"}" class="wide-view-tab">活跃监控</button>
      <button type="button" role="tab" data-nav-tab="history" aria-selected="${activeTab === "history" ? "true" : "false"}" class="wide-view-tab">
        <span>历史对话</span>
        ${closed24hCount > 0 ? `<span class="tab-count-pill tabular-nums">${closed24hCount}</span>` : ""}
      </button>
      <button type="button" role="tab" data-nav-tab="stats" aria-selected="${activeTab === "stats" ? "true" : "false"}" class="wide-view-tab">统计面板</button>
      <button type="button" role="tab" data-nav-tab="models" aria-selected="${activeTab === "models" ? "true" : "false"}" class="wide-view-tab">模型与配置${updateDot}</button>
    </div>

    <div class="topbar-right">
      ${renderCodexToggleButton(codexEnabled, "topbar")}

      <span class="slot-pill tabular-nums" title="${escapeHtml(slotTooltip)}">
        <span class="status-dot ${globalActiveRuns > 0 ? "is-running" : "is-done"}" aria-hidden="true"></span>
        <span>${globalActiveRuns}/${maxConcurrentRuns}</span>
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
        ${updateDot}
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
      </div>`
    : "";

  const wideStripHtml = isMonitor
    ? `<div id="wide-subagent-strip" class="wide-only-nav wide-subagent-strip">
        <div id="wide-agent-tablist" class="agent-tablist" role="tablist" aria-label="活跃子 Agent 对话标签">${wideTablistHtml}</div>
        <div class="wide-subagent-right">
          ${wideScopePillHtml}
          <span class="wide-subagent-hint">活跃 Codex 会话 (留存 ${retentionHours}h)</span>
          <button type="button" id="wide-new-chat-btn" data-open-composer class="btn btn-primary btn-sm">+ 新建对话</button>
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
      scope: scopeInfo,
      composer: composerOpen ? {
        draft: getComposerDraft(),
        models: getComposerModels(),
        sending: composerSending,
        error: composerError,
      } : null,
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
    });
  } else if (activeTab === "stats") {
    bodyHtml = renderStatsView({
      range: statsRange,
      stats: statsPayload,
      loading: statsLoading,
      loadError: statsLoadError,
      retentionHours,
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
      update: getUpdateViewState(),
    });
  }

  const narrowSwitcherHtml = isMonitor ? renderNarrowAgentSwitcher(panelRuns, activeRunId, narrowPickerOpen, scopeInfo) : "";
  root.innerHTML = `${topbarHtml}<div id="panel-update-region">${renderUpdateBanner()}</div>${narrowSwitcherHtml}${pausedBannerHtml}${wideStripHtml}<div class="panel-body">${bodyHtml}</div>`;
  bindInteractions();
  restoreTimelineScroll();
  if (narrowPickerOpen) {
    const popover = document.getElementById("narrow-agent-popover");
    if (popover) popover.scrollTop = pickerScroll;
  }
  const newComposerBox = document.getElementById("user-task-drawer");
  if (newComposerBox) newComposerBox.scrollTop = composerScrollTop;
  if (composerFocus) {
    const field = document.getElementById(composerFocus);
    field?.focus({ preventScroll: true });
    if ((field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement) &&
        composerSelection && composerSelection[0] !== null && composerSelection[1] !== null) {
      field.setSelectionRange(composerSelection[0], composerSelection[1]);
    }
  } else if (switcherFocusId) {
    const button = document.getElementById(switcherFocusId) ??
      (narrowPickerOpen ? document.querySelector<HTMLElement>('#narrow-agent-options [aria-selected="true"]') : null) ??
      document.getElementById("narrow-agent-picker-btn");
    button?.focus({ preventScroll: true });
  } else if (wideTabFocusId) {
    document.getElementById(wideTabFocusId)?.focus({ preventScroll: true });
  }
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

  paintManualRefreshButton();
  document.getElementById("panel-refresh-btn")?.addEventListener("click", () => void handleManualRefresh());
  bindComposer();
  bindUpdateActions(document);

  document.querySelectorAll<HTMLInputElement | HTMLButtonElement>(
    "[data-toggle-codex-master], #cfg-intercept-switch, #cfg-orch-switch, #save-workbench-btn",
  ).forEach((btn) => { btn.disabled = configWritePending; });

  document.querySelectorAll<HTMLElement>("[data-toggle-codex-master]").forEach((btn) => {
    btn.addEventListener("click", () => {
      void toggleConfigFlag("enabled", !codexEnabled, "toggle_codex_enabled", [
        "已开启 Codex 子代理联动，通用子任务将默认走 Codex",
        "已暂停 Codex 子代理联动，新子任务已全部交由 Antigravity 原生子代理执行",
      ]);
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

  document.getElementById("btn-archive-all")?.addEventListener("click", () => {
    void handleCloseAllFinished();
  });

  document.getElementById("empty-to-history")?.addEventListener("click", () => {
    switchTab("history");
  });

  document.querySelectorAll<HTMLElement>("[data-toggle-scope]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!scopeSessionId) return;
      scopeMode = scopeMode === "session" ? "all" : "session";
      narrowPickerOpen = false;
      monitorRunMutationSeq++;
      render();
      void refreshMonitorRuns(true).then(() => {
        showToast(
          scopeMode === "session"
            ? `已按本对话隔离显示 (${panelRuns.length} 个任务)`
            : `已切换为显示全部并行对话 (${panelRuns.length} 个任务)`,
        );
      }).catch((err) => showToast(`切换会话过滤失败: ${errorText(err)}`));
    });
  });

  const picker = document.getElementById("narrow-agent-picker-btn");
  picker?.addEventListener("click", () => setNarrowPickerOpen(!narrowPickerOpen));
  picker?.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setNarrowPickerOpen(true);
    }
  });
  const stepAgent = (offset: number) => {
    if (panelRuns.length < 2) return;
    const index = Math.max(0, panelRuns.findIndex((run) => run.run_id === activeRunId));
    const target = panelRuns[(index + offset + panelRuns.length) % panelRuns.length];
    setNarrowPickerOpen(false);
    void selectRun(target.run_id);
  };
  document.getElementById("narrow-agent-prev")?.addEventListener("click", () => stepAgent(-1));
  document.getElementById("narrow-agent-next")?.addEventListener("click", () => stepAgent(1));
  document.getElementById("narrow-archive-finished")?.addEventListener("click", () => void handleCloseAllFinished());
  const options = Array.from(document.querySelectorAll<HTMLElement>("[data-narrow-run]"));
  options.forEach((option, index) => {
    option.addEventListener("click", () => {
      const id = option.getAttribute("data-narrow-run");
      setNarrowPickerOpen(false, true);
      if (id && id !== activeRunId) void selectRun(id);
    });
    option.addEventListener("keydown", (event) => {
      let nextIndex: number;
      if (event.key === "ArrowDown") nextIndex = (index + 1) % options.length;
      else if (event.key === "ArrowUp") nextIndex = (index + options.length - 1) % options.length;
      else if (event.key === "Home") nextIndex = 0;
      else if (event.key === "End") nextIndex = options.length - 1;
      else return;
      event.preventDefault();
      options[nextIndex].focus();
    });
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
        const next = tabs[nextIdx];
        const nextId = next.getAttribute("data-run-id");
        if (nextId) {
          if (nextId !== activeRunId) void selectRun(nextId);
          const focused = document.getElementById(next.id);
          focused?.focus({ preventScroll: true });
          focused?.scrollIntoView({ block: "nearest", inline: "nearest" });
        }
      }
    });
  });

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

  const stopBtn = document.getElementById("stop-run-btn");
  stopBtn?.addEventListener("click", async () => {
    const id = stopBtn.getAttribute("data-stop-run") || activeRunId;
    if (!id) return;
    try {
      const res = await app.callServerTool({ name: "cancel_run", arguments: { run_id: id } });
      if (res?.isError) throw new Error(extractToolErrorMessage(res, "停止任务失败"));
      if (activeRunId === id) await fetchIncrementalEvents();
      showToast("已发送停止信号");
    } catch {
      showToast("停止任务失败");
    }
  });

  document.getElementById("send-review-msg-btn")?.addEventListener("click", async () => {
    if (!currentRun) return;
    const resObj =
      currentRun.result && typeof currentRun.result === "object" ? currentRun.result : null;
    const files = (Array.isArray(resObj?.changed_files)
      ? resObj.changed_files.filter((file) => typeof file === "string").join(", ") : "") || "无变更文件";
    const wt = currentRun.worktree?.branch ? `，分支 ${currentRun.worktree.branch}` : "";
    const rawAnswer = typeof currentRun.result === "string" ? currentRun.result : resObj?.summary;
    const reply = rawAnswer ? `Codex 回复${rawAnswer.length > 4000 ? "（节选）" : ""}: ${rawAnswer.slice(0, 4000)}` : "";
    const userPrompt = currentRun.prompt || currentRun.prompt_preview;
    const reviewRunId = currentRun.run_id;
    const msg = currentRun.origin === "user"
      ? [
          `请复核 Codex 直连会话 ${reviewRunId}（工作目录 ${currentRun.cwd}${wt}）。如果涉及代码变更，请检查 diff 并验证测试。`,
          userPrompt ? `用户指令: ${userPrompt.slice(0, 1000)}` : "",
          reply,
        ].filter(Boolean).join("\n")
      : `请复核 Codex 子任务 ${reviewRunId}（模型 ${currentRun.model || "默认"}${wt}，改动: ${files}），检查 diff 并验证测试。`;
    try {
      const result = await app.sendMessage({ role: "user", content: [{ type: "text", text: msg }] });
      if (result?.isError) throw new Error("主 Agent 未接收消息");
      if (currentRun?.run_id !== reviewRunId) return;
      actionFeedback = "已发送复核请求";
      render();
    } catch (err) {
      showToast(`发送复核请求失败: ${errorText(err)}`);
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
      const previousRun = currentRun?.run_id === id ? currentRun : panelRuns.find((run) => run.run_id === id);
      followupSending = true;
      render();
      try {
        const res = await app.callServerTool({
          name: "send_followup",
          arguments: { run_id: id, prompt },
        });
        const snapshot = res?.structuredContent as RunSnapshot | undefined;
        if (res?.isError || !snapshot?.run_id) throw new Error(extractToolErrorMessage(res, "服务端未返回追问任务"));
        if (!resourcesActive) return;
        const newRun = { origin: previousRun?.origin, raw: previousRun?.raw, ...snapshot };
        composerDrafts.delete(id);
        monitorRunMutationSeq++;
        panelRuns = [newRun, ...panelRuns.filter((r) => r.run_id !== newRun.run_id)];
        activeRunsCount = panelRuns.filter((r) => r.status === "running").length;
        activeTab = "subagents";
        resetMonitorSelection(newRun.run_id, newRun);
        resetRunTimeline(newRun);
        syncPollingTimers();
        showToast(`已发起追问，切换至新执行「${getRunShortTitle(newRun)}」`);
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
      if (!resourcesActive) return;
      const pos = histSearch.selectionStart ?? histSearch.value.length;
      historyQuery = histSearch.value;
      if (historySearchTimer !== null) window.clearTimeout(historySearchTimer);
      historySearchTimer = window.setTimeout(() => {
        historySearchTimer = null;
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
    void toggleConfigFlag("intercept_native_subagents", (ev.target as HTMLInputElement).checked,
      "toggle_intercept_native_subagents", ["已启用默认接管原生子代理路由", "已改为仅按需使用 Codex（不自动拦截原生子代理）"]);
  });

  document.getElementById("cfg-orch-switch")?.addEventListener("change", (ev) => {
    void toggleConfigFlag("exclude_orchestrator_models", (ev.target as HTMLInputElement).checked,
      "toggle_exclude_orchestrator", ["已开启 Orchestrator 模型排除保护", "已解除 Orchestrator 模型派发限制"]);
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
    if (!workbenchDraft || !beginConfigWrite()) return;
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
      if (res?.isError || !data?.saved) throw new Error(extractToolErrorMessage(res, "服务端未确认配置已保存"));
      workbenchData = data;
      workbenchDraft = cloneConfig(data.saved);
      maxConcurrentRuns = data.max_concurrent_runs || maxConcurrentRuns;
      if (typeof data.retention_hours === "number") retentionHours = data.retention_hours;
      applyConfigFlags({ ...data.saved, ...data });
      configStatusMsg = "配置已保存并立即生效";
    } catch (err) {
      configErrorMsg = String(err);
    } finally {
      savingConfig = false;
      endConfigWrite();
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
      const preview = res?.structuredContent as Partial<CommandPreviewPayload> | undefined;
      previewPayload = typeof preview?.command_line === "string" ? preview as CommandPreviewPayload : null;
      render();
    } catch (err) {
      configErrorMsg = String(err);
      render();
    }
  });

  document.getElementById("add-preset-btn")?.addEventListener("click", () => {
    syncDraftFromDom();
    if (!workbenchDraft) return;
    let index = 1;
    while (Object.prototype.hasOwnProperty.call(workbenchDraft.presets, `custom_${index}`)) index++;
    const baseName = `custom_${index}`;
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

function activateUiResources(): void {
  resourcesActive = true;
  if (appConnected && !disconnectSizeObserver) disconnectSizeObserver = app.setupSizeChangedNotifications();
}

// Register handlers BEFORE connect()
app.ontoolinput = ({ arguments: args }) => {
  const a = (args || {}) as Record<string, unknown>;
  if (typeof a.prompt === "string") {
    activateUiResources();
    viewMode = "inline";
    stopUpdatePolling();
    narrowPickerOpen = false;
    const pendingRun: RunSnapshot = {
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
    resetMonitorSelection(null, pendingRun);
    syncPollingTimers();
    render();
  }
};

app.ontoolresult = (result) => {
  const sc = (result?.structuredContent || {}) as Record<string, unknown>;
  if (sc.view === "panel") {
    activateUiResources();
    viewMode = "panel";
    narrowPickerOpen = false;
    activeTab =
      sc.tab === "history" || sc.tab === "stats" || sc.tab === "models" || sc.tab === "subagents"
        ? sc.tab
        : "subagents";
    monitorRunMutationSeq++;
    panelRuns = Array.isArray(sc.runs) ? (sc.runs as RunSnapshot[]) : [];
    const incomingScopeId = typeof sc.scope_session_id === "string" && sc.scope_session_id.trim()
      ? sc.scope_session_id.trim() : null;
    scopeSessionId = incomingScopeId;
    scopeSessionTitle = typeof sc.scope_session_title === "string" && sc.scope_session_title.trim()
      ? sc.scope_session_title.trim() : null;
    scopeMode = incomingScopeId && sc.scope_mode !== "all" ? "session" : "all";
    if (typeof sc.default_cwd === "string" && sc.default_cwd.trim()) defaultCwd = sc.default_cwd.trim();
    activeRunsCount = Number(sc.active_runs ?? panelRuns.filter((r) => r.status === "running").length);
    globalActiveRuns = Number(sc.global_active_runs ?? activeRunsCount);
    totalUnclosedRuns = Number(sc.total_unclosed_runs ?? panelRuns.length);
    otherSessionsRuns = Number(sc.other_sessions_runs ?? 0);
    closed24hCount = Number(sc.closed_24h_count ?? closed24hCount);
    maxConcurrentRuns = Number(sc.max_concurrent_runs ?? 5);
    if (!configWritePending) applyConfigFlags(sc as ConfigFlags);
    if (typeof sc.retention_hours === "number") retentionHours = sc.retention_hours;

    const targetId =
      typeof sc.selected_run_id === "string" && sc.selected_run_id
        ? sc.selected_run_id
        : panelRuns[0]?.run_id || null;

    resetMonitorSelection(targetId, panelRuns.find((r) => r.run_id === targetId) ?? null);
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
        void selectRun(targetId);
      } else {
        syncPollingTimers();
        render();
      }
    }
    if (!updateInitialized) {
      updateInitialized = true;
      void checkForUpdates({ force: false });
      void refreshUpdateStatus().catch(() => { /* 更新卡展示错误；不阻塞首屏。 */ });
    }
    scheduleUpdatePolling();
    return;
  }

  if (typeof sc.run_id === "string") {
    activateUiResources();
    viewMode = "inline";
    stopUpdatePolling();
    narrowPickerOpen = false;
    stopPanelRefresh();
    const run = sc as unknown as RunSnapshot;
    resetMonitorSelection(run.run_id, run);
    resetRunTimeline(run);
    syncPollingTimers();
    render();
  }
};

function cleanupUiResources(): void {
  resourcesActive = false;
  stopUpdatePolling();
  updateLifecycleSeq++;
  updateStatusSeq++;
  updateCheckSeq++;
  updateStatusRequest = null;
  updateInitialized = updateChecking = updateStarting = false;
  selectRunSeq++;
  historyDetailSeq++;
  for (const queue of [monitorRefresh, historyRefresh, statsRefresh, configRefresh]) queue.cancelPending();
  stopPolling();
  stopPanelRefresh();
  for (const timer of [historySearchTimer, toastTimer, interestOpenTimer, interestCloseTimer]) {
    if (timer !== null) window.clearTimeout(timer);
  }
  historySearchTimer = toastTimer = interestOpenTimer = interestCloseTimer = null;
  try {
    interestPolyfillActiveTip?.hidePopover?.();
  } catch {
    // 提示节点可能已经被重绘移除。
  }
  interestPolyfillActiveTip = null;
  disconnectSizeObserver?.();
  disconnectSizeObserver = null;
  document.getElementById("toast-bar")?.classList.add("is-hidden");
  setNarrowPickerOpen(false);
  disconnectAllTablistObservers();
}

app.ontoolcancelled = cleanupUiResources;
app.onteardown = () => {
  cleanupUiResources();
  return {};
};

app.onhostcontextchanged = (ctx) => {
  if (ctx.theme) applyDocumentTheme(ctx.theme);
  if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
};

function handlePageWakeup(): void {
  if (!resourcesActive || document.hidden) return;
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
    stopUpdatePolling();
  } else {
    scheduleUpdatePolling();
    handlePageWakeup();
  }
});

window.addEventListener("focus", () => {
  if (!document.hidden) {
    handlePageWakeup();
  }
});

document.addEventListener("pointerdown", (event) => {
  if (narrowPickerOpen && !(event.target as Element | null)?.closest?.(".narrow-agent-switcher-bar")) {
    setNarrowPickerOpen(false);
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || event.isComposing || event.defaultPrevented) return;
  if (narrowPickerOpen) {
    event.preventDefault();
    setNarrowPickerOpen(false, true);
  } else if (viewMode === "panel" && activeTab === "subagents" && composerOpen && !composerSending) {
    event.preventDefault();
    closeComposer();
  }
});

async function init(): Promise<void> {
  try {
    await app.connect();
    appConnected = true;
    if (resourcesActive) activateUiResources();
    const ctx = app.getHostContext();
    if (ctx?.theme) applyDocumentTheme(ctx.theme);
    if (ctx?.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
  } catch (err) {
    const root = document.getElementById("root");
    if (!root) return;
    root.innerHTML = `<div class="load-error-card" role="alert">
      <div>连接 Agy Codex MCP 主机失败: ${escapeHtml(err instanceof Error ? err.message : String(err))}</div>
      <div class="load-error-actions"><button type="button" id="retry-connect-btn" class="btn btn-primary btn-sm">重试连接</button></div>
    </div>`;
    document.getElementById("retry-connect-btn")?.addEventListener("click", () => {
      root.innerHTML = '<div class="empty-hint" role="status">正在重新连接 Agy Codex MCP 主机...</div>';
      void init();
    });
  }
}

void init();
