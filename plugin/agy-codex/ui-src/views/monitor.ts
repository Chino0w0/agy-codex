import type { RunSnapshot } from "../types";
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

export interface MonitorViewState {
  runs: RunSnapshot[];
  activeRunId: string | null;
  currentRun: RunSnapshot | null;
  traceState: TraceViewState;
  retentionHours: number;
}

export function renderMonitorTablist(
  runs: RunSnapshot[],
  activeRunId: string | null,
  idPrefix: string,
  retentionHours = 24,
): string {
  if (runs.length === 0) {
    return `<span class="tablist-empty">当前无活跃子 Agent</span>`;
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
    <div id="narrow-agent-popover" class="custom-scroll"${open ? "" : " hidden"}>
      <div class="switcher-popover-head">
        <span id="narrow-agent-options-label">活跃子 Agent (共 ${counts})</span>
        ${runningCount < runs.length ? `<button type="button" id="narrow-archive-finished" class="btn btn-sm">清空已完成</button>` : ""}
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

export function renderMonitorView(st: MonitorViewState): string {
  if (st.runs.length === 0) {
    return `<div class="empty-state-center">
      <div class="empty-state-icon" aria-hidden="true">
        <svg width="20" height="20" viewBox="0 0 16 16" fill="none">
          <path d="M3 8l3.2 3.2L13 4.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
      <div class="empty-state-title">当前无活跃 Codex 子 Agent</div>
      <p class="empty-state-desc">所有已关闭的任务均按 Antigravity 大会话归档保存在「历史对话」中（日志留存 ${st.retentionHours} 小时），方便后续 Agent 追溯排查。</p>
      <div class="empty-state-actions">
        <button type="button" id="empty-to-history" class="btn btn-primary">查看历史对话</button>
      </div>
    </div>`;
  }

  const targetRun =
    st.currentRun && st.currentRun.run_id === st.activeRunId
      ? st.currentRun
      : st.runs.find((r) => r.run_id === st.activeRunId) || st.runs[0];

  if (!targetRun) {
    return `<div class="empty-hint">正在加载子 Agent 运行日志...</div>`;
  }

  return `<div role="tabpanel" id="panel-${escapeHtml(targetRun.run_id)}" class="monitor-tabpanel">
    ${renderTraceView(targetRun, st.traceState, { readOnly: false })}
  </div>`;
}
