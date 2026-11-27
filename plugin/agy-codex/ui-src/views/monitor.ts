import type { CatalogModel, RunSnapshot } from "../types";
import {
  ICONS,
  escapeHtml,
  formatDuration,
  getRunDisplayTitle,
  getRunRole,
  getRunShortTitle,
  getRunStatusMeta,
  statusDotClass,
} from "../utils";
import { renderTraceView, type TraceViewState } from "./trace";

export interface UserTaskDraft {
  prompt: string;
  cwd: string;
  model: string;
  effort: string;
  mode: "write" | "read-only";
  worktree: string;
  persist: boolean;
}

export interface MonitorScopeInfo {
  sessionId: string | null;
  sessionTitle: string | null;
  mode: "session" | "all";
  otherSessionsRuns: number;
  totalUnclosedRuns: number;
}

export interface MonitorViewState {
  runs: RunSnapshot[];
  activeRunId: string | null;
  currentRun: RunSnapshot | null;
  traceState: TraceViewState;
  retentionHours: number;
  scope?: MonitorScopeInfo;
  composer: {
    draft: UserTaskDraft;
    models: CatalogModel[];
    sending: boolean;
    error: string | null;
  } | null;
}

export function renderMonitorTablist(
  runs: RunSnapshot[],
  activeRunId: string | null,
  idPrefix: string,
  retentionHours = 24,
): string {
  if (runs.length === 0) {
    return `<span class="tablist-empty">当前无活跃会话</span>`;
  }
  return runs
    .map((run) => {
      const isSel = run.run_id === activeRunId;
      const isRunning = run.status === "running";
      const shortTitle = getRunShortTitle(run);
      const role = getRunRole(run);
      const model = run.observed?.model || run.model || run.effective?.model || "默认模型";
      const closeBtnHtml = isRunning
        ? ""
        : `<button type="button" class="tab-close-btn" tabindex="-1" data-close-run="${escapeHtml(run.run_id)}" title="关闭并归档 (留存 ${retentionHours}h)" aria-label="关闭并归档 ${escapeHtml(shortTitle)}">${ICONS.closeX}</button>`;
      return `<div class="tab-unit${isSel ? " is-selected" : ""}${isRunning ? " is-running-tab" : ""}" role="presentation">
        <button type="button" role="tab" id="${escapeHtml(idPrefix)}-tab-${escapeHtml(run.run_id)}" data-run-id="${escapeHtml(run.run_id)}" aria-selected="${isSel ? "true" : "false"}" tabindex="${isSel ? "0" : "-1"}" class="agent-tab-btn" title="${escapeHtml(`${shortTitle} | ${role} | ${model}`)}">
          <span class="${statusDotClass(run.status)}" aria-hidden="true"></span>
          <span class="tab-label-text">${escapeHtml(shortTitle)}</span>
        </button>
        ${closeBtnHtml}
      </div>`;
    })
    .join("");
}

function getRunAlert(run: RunSnapshot): { label: string; badgeClass: string } | null {
  if (run.status === "error" || run.health === "error") return getRunStatusMeta("error");
  if (run.health === "stalled") return { label: "输出停滞", badgeClass: "badge-warn" };
  if (run.health === "loop_warning") return { label: "疑似循环", badgeClass: "badge-warn" };
  if (run.status === "partial" || run.status === "blocked") return getRunStatusMeta(run.status);
  return null;
}

export function renderNarrowAgentSwitcher(
  runs: RunSnapshot[],
  activeRunId: string | null,
  open: boolean,
  scope?: MonitorScopeInfo,
): string {
  const activeRun = runs.find((run) => run.run_id === activeRunId) ?? runs[0];
  if (!activeRun) return "";
  const activeTitle = getRunDisplayTitle(activeRun);
  const runningCount = runs.filter((run) => run.status === "running").length;
  const counts = `${runs.length} 个 | ${runningCount} 运行中`;
  const otherAlerts = runs.filter((run) => run.run_id !== activeRun.run_id).map(getRunAlert);
  const hasError = otherAlerts.some((alert) => alert?.badgeClass === "badge-err");
  const alertDot = otherAlerts.some(Boolean)
    ? `<span class="switcher-alert-dot${hasError ? " is-error" : ""}" role="img" aria-label="其他任务有异常" title="其他任务有异常"></span>`
    : "";
  const scopeLabel = scope?.sessionId
    ? (scope.mode === "session" ? "当前对话子 Agent" : "全部对话子 Agent")
    : "活跃子 Agent";
  const scopeActionBtn = scope?.sessionId
    ? (scope.mode === "session" && scope.otherSessionsRuns > 0
      ? `<button type="button" data-toggle-scope class="link-btn">查看全部 (+${scope.otherSessionsRuns})</button>`
      : scope.mode === "all"
        ? `<button type="button" data-toggle-scope class="link-btn">仅看本对话</button>`
        : "")
    : "";

  return `<div class="narrow-agent-switcher-bar">
    <button type="button" id="narrow-agent-picker-btn" aria-haspopup="listbox" aria-controls="narrow-agent-options" aria-expanded="${open}" title="${escapeHtml(activeTitle)}">
      <span class="${statusDotClass(activeRun.status)}" aria-hidden="true"></span>
      <span class="switcher-current-title">${escapeHtml(activeTitle)}</span>
      <span class="tab-count-pill switcher-count">${counts}${alertDot}</span>
      <span aria-hidden="true">▾</span>
    </button>
    ${runs.length > 1 ? `
      <button type="button" id="narrow-agent-prev" class="icon-btn switcher-step" aria-label="上一子任务" title="上一子任务">&lt;</button>
      <button type="button" id="narrow-agent-next" class="icon-btn switcher-step" aria-label="下一子任务" title="下一子任务">&gt;</button>
    ` : ""}
    <button type="button" id="open-new-chat-btn" data-open-composer class="icon-btn switcher-new-chat" aria-label="直接新建 Codex 对话" title="直接新建 Codex 对话">+</button>
    <div id="narrow-agent-popover" class="custom-scroll"${open ? "" : " hidden"}>
      <div class="switcher-popover-head">
        <span id="narrow-agent-options-label">${scopeLabel} (共 ${counts})</span>
        <div class="switcher-popover-actions">
          ${scopeActionBtn}
          ${runningCount < runs.length ? `<button type="button" id="narrow-archive-finished" class="btn btn-sm">清空已完成</button>` : ""}
        </div>
      </div>
      <div id="narrow-agent-options" role="listbox" aria-labelledby="narrow-agent-options-label">
        ${runs.map((run) => {
          const selected = run.run_id === activeRun.run_id;
          const id = escapeHtml(run.run_id);
          const title = escapeHtml(getRunDisplayTitle(run, Infinity));
          const model = run.observed?.model || run.model || run.effective?.model || "默认模型";
          const effort = run.observed?.effort || run.effort || run.effective?.effort || "默认";
          const alert = getRunAlert(run);
          const meta = `${model} | ${effort} | ${run.status === "running" ? "已运行" : "用时"} ${formatDuration(run.duration_sec ?? run.elapsed_sec ?? 0)}`;
          return `<div class="switcher-option-row${selected ? " is-selected" : ""}" role="presentation">
            <button type="button" role="option" id="narrow-option-${id}" data-narrow-run="${id}" aria-selected="${selected}" tabindex="${selected ? 0 : -1}" class="switcher-option">
              <span class="${statusDotClass(run.status)}" aria-hidden="true"></span>
              <span class="switcher-option-copy">
                <span class="switcher-option-title">${title}</span>
                <span class="switcher-option-badges">
                  <span class="badge badge-info">${escapeHtml(getRunRole(run))}</span>
                  ${alert ? `<span class="badge ${alert.badgeClass}" title="${escapeHtml(run.health_reason || alert.label)}">${alert.label}</span>` : ""}
                </span>
                <span class="switcher-option-meta trace-muted">${escapeHtml(meta)}</span>
              </span>
              ${selected ? `<span class="switcher-selected-mark" aria-hidden="true">✓</span>` : ""}
            </button>
            ${run.status !== "running" ? `<button type="button" id="narrow-archive-${id}" class="icon-btn" data-close-run="${id}" aria-label="关闭并归档 ${title}" title="关闭并归档">${ICONS.closeX}</button>` : ""}
          </div>`;
        }).join("")}
      </div>
    </div>
  </div>`;
}

function renderComposer({ draft, models, sending, error }: NonNullable<MonitorViewState["composer"]>): string {
  const option = (value: string, label: string, selected: string) =>
    `<option value="${escapeHtml(value)}"${value === selected ? " selected" : ""}>${escapeHtml(label)}</option>`;
  return `<section id="user-task-drawer" class="user-task-drawer custom-scroll" aria-labelledby="composer-title">
    <form id="user-task-form">
      <div class="composer-head">
        <strong id="composer-title">直接发起 Codex 对话</strong>
        <span class="trace-muted">不经过主 Agent</span>
      </div>
      <fieldset${sending ? " disabled" : ""}>
        <label class="composer-field" for="composer-prompt">任务指令
          <textarea id="composer-prompt" rows="2" placeholder="告诉 Codex 要做什么…">${escapeHtml(draft.prompt)}</textarea>
        </label>
        <label class="composer-field" for="composer-cwd">工作目录
          <input id="composer-cwd" type="text" value="${escapeHtml(draft.cwd)}" placeholder="工作区的绝对路径" spellcheck="false" />
        </label>
        <div class="composer-grid">
          <label class="composer-field" for="composer-model">模型
            <select id="composer-model">
              ${option("", "继承默认", draft.model)}
              ${draft.model && !models.some((model) => model.slug === draft.model) ? `<option value="${escapeHtml(draft.model)}" selected disabled>${escapeHtml(draft.model)}（已不可用）</option>` : ""}
              ${models.map((model) => option(model.slug, model.display_name || model.slug, draft.model)).join("")}
            </select>
          </label>
          <label class="composer-field" for="composer-effort">思考强度
            <select id="composer-effort">
              ${option("", "默认", draft.effort)}
              ${["low", "medium", "high", "xhigh"].map((level) => option(level, level, draft.effort)).join("")}
            </select>
          </label>
          <label class="composer-field" for="composer-mode">运行模式
            <select id="composer-mode">${option("write", "可写", draft.mode)}${option("read-only", "只读", draft.mode)}</select>
          </label>
          <label class="composer-field" for="composer-worktree">Worktree（可选）
            <input id="composer-worktree" type="text" value="${escapeHtml(draft.worktree)}" placeholder="分支名称" spellcheck="false" />
          </label>
        </div>
        <label class="composer-persist"><input id="composer-persist" type="checkbox"${draft.persist ? " checked" : ""} /> 多轮续聊 (persist=true)</label>
      </fieldset>
      ${error ? `<div class="banner-warn" role="alert">${escapeHtml(error)}</div>` : ""}
      <div class="composer-actions">
        <button type="button" id="cancel-user-task-btn" class="btn btn-sm"${sending ? " disabled" : ""}>取消</button>
        <button type="submit" class="btn btn-primary btn-sm" title="Ctrl+Enter / Cmd+Enter"${sending ? " disabled" : ""}>${sending ? "正在发起…" : "发起对话"}</button>
      </div>
    </form>
  </section>`;
}

export function renderMonitorView(st: MonitorViewState): string {
  const composerHtml = st.composer ? renderComposer(st.composer) : "";
  if (st.runs.length === 0) {
    const isolatedOther = Boolean(st.scope?.sessionId && st.scope.mode === "session" && st.scope.otherSessionsRuns > 0);
    const otherCount = st.scope?.otherSessionsRuns ?? 0;
    return `${composerHtml}<div class="empty-state-center${st.composer ? " is-under-composer" : ""}">
      <div class="empty-state-icon" aria-hidden="true">
        <svg width="20" height="20" viewBox="0 0 16 16" fill="none">
          <path d="M3 8l3.2 3.2L13 4.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
      <div class="empty-state-title">${isolatedOther ? "当前对话暂无活跃 Codex 会话" : "当前无活跃 Codex 会话"}</div>
      <p class="empty-state-desc">${
        isolatedOther
          ? `其他并行 Antigravity 对话中另有 ${otherCount} 个活跃任务已自动隔离；你也可以随时点击下方切换查看全部对话。`
          : `所有已关闭的任务均按 Antigravity 大会话归档保存在「历史对话」中（日志留存 ${st.retentionHours} 小时），方便后续 Agent 追溯排查。`
      }</p>
      <div class="empty-state-actions">
        <button type="button" id="empty-new-chat-btn" data-open-composer class="btn btn-primary">+ 直接发起 Codex 对话</button>
        ${isolatedOther ? `<button type="button" id="empty-show-all-scope" data-toggle-scope class="btn">查看全部对话 (${otherCount})</button>` : ""}
        <button type="button" id="empty-to-history" class="btn">查看历史对话</button>
      </div>
    </div>`;
  }

  const targetRun =
    st.currentRun && st.currentRun.run_id === st.activeRunId
      ? st.currentRun
      : st.runs.find((r) => r.run_id === st.activeRunId) || st.runs[0];

  if (!targetRun) {
    return `${composerHtml}<div class="empty-hint">正在加载子 Agent 运行日志...</div>`;
  }

  return `${composerHtml}<div role="tabpanel" id="panel-${escapeHtml(targetRun.run_id)}" class="monitor-tabpanel">
    ${renderTraceView(targetRun, st.traceState, { readOnly: false })}
  </div>`;
}
