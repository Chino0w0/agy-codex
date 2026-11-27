import type { RunSnapshot } from "../types";
import {
  ICONS,
  escapeHtml,
  getRunRole,
  getRunShortTitle,
  renderCodexToggleButton,
  statusDotClass,
} from "../utils";
import { renderTraceView, type TraceViewState } from "./trace";

export interface MonitorViewState {
  runs: RunSnapshot[];
  activeRunId: string | null;
  currentRun: RunSnapshot | null;
  traceState: TraceViewState;
  retentionHours: number;
  enabled: boolean;
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
        ${renderCodexToggleButton(st.enabled, "inline")}
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
    <div class="monitor-linkage-bar narrow-only-nav">
      <span class="trace-muted">Antigravity 联动子 Agent</span>
      ${renderCodexToggleButton(st.enabled, "inline")}
    </div>
    ${renderTraceView(targetRun, st.traceState, { readOnly: false })}
  </div>`;
}
