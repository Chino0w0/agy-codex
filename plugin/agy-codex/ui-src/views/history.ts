import type { HistoryPayload, RunSnapshot } from "../types";
import {
  escapeHtml,
  formatDuration,
  formatTimestamp,
  formatTokensBadge,
  formatTtlRemaining,
  getRunDisplayTitle,
  getRunRole,
  getRunStatusMeta,
  getRunWorkDigest,
  renderCodexToggleButton,
  statusDotClass,
} from "../utils";
import { renderTraceView, type TraceViewState } from "./trace";

export type HistoryFilter = "all" | "closed" | "active" | "issue";

function matchesHistoryFilter(run: RunSnapshot, filter: HistoryFilter): boolean {
  if (filter === "closed") return Boolean(run.closed);
  if (filter === "active") return !run.closed && !run.expired;
  if (filter === "issue") {
    return (
      run.status === "error" ||
      run.status === "blocked" ||
      run.status === "cancelled" ||
      run.status === "partial"
    );
  }
  return true;
}

export interface HistoryViewState {
  query: string;
  filter: HistoryFilter;
  payload: HistoryPayload | null;
  loading: boolean;
  loadError?: string | null;
  selectedRunId: string | null;
  selectedRunDetail: RunSnapshot | null;
  inspecting: boolean;
  openSessionIds: Set<string>;
  traceState: TraceViewState;
  retentionHours: number;
  enabled: boolean;
}

export function renderHistoryView(st: HistoryViewState): string {
  const rawSessions = st.payload?.sessions || [];
  const sessions = rawSessions
    .map((s) => ({
      ...s,
      runs: (s.runs || []).filter((r) => matchesHistoryFilter(r, st.filter)),
    }))
    .filter((s) => s.runs.length > 0);
  const retentionHours = st.payload?.retention_hours ?? st.retentionHours;

  const allRuns: RunSnapshot[] = [];
  for (const s of sessions) {
    for (const r of s.runs || []) {
      allRuns.push(r);
    }
  }

  let selectedRun: RunSnapshot | null = null;
  if (st.selectedRunDetail && st.selectedRunDetail.run_id === st.selectedRunId) {
    selectedRun = st.selectedRunDetail;
  } else if (st.selectedRunId) {
    selectedRun = allRuns.find((r) => r.run_id === st.selectedRunId) || null;
  }
  if (!selectedRun && allRuns.length > 0) {
    selectedRun = allRuns[0];
  }

  const allOpen = sessions.length > 0 && sessions.every((s) => st.openSessionIds.has(s.session_id));

  const filterPills: Array<{ key: HistoryFilter; label: string }> = [
    { key: "all", label: "全部" },
    { key: "closed", label: "24h 归档" },
    { key: "active", label: "活跃中" },
    { key: "issue", label: "仅异常" },
  ];

  const toolbarHtml = `<div class="history-toolbar">
    <div class="history-search-row">
      <input id="history-search-input" type="search" value="${escapeHtml(st.query)}" placeholder="搜索 Antigravity 大会话、子 Agent 角色或工作摘要..." />
      <button type="button" id="acc-toggle-all" class="btn btn-sm" aria-expanded="${allOpen ? "true" : "false"}">${allOpen ? "全部收起" : "全部展开"}</button>
    </div>
    <div class="history-filter-row">
      ${filterPills
        .map(
          (fp) =>
            `<button type="button" class="pill${st.filter === fp.key ? " active" : ""}" data-hfilter="${fp.key}">${escapeHtml(fp.label)}</button>`,
        )
        .join("")}
      <div class="history-filter-right">
        ${renderCodexToggleButton(st.enabled, "inline")}
        ${
          st.payload
            ? `<span class="history-total-hint">共 ${st.payload.total_runs} 条记录 (留存 ${retentionHours}h)</span>`
            : ""
        }
      </div>
    </div>
  </div>`;

  let accordionHtml = "";
  if (st.loadError && !st.payload) {
    accordionHtml = `<div class="load-error-card">
      <div>加载历史对话失败: ${escapeHtml(st.loadError)}</div>
      <div class="load-error-actions">
        <button type="button" id="retry-history-btn" class="btn btn-primary btn-sm">重试加载</button>
      </div>
    </div>`;
  } else if (st.loading && !st.payload) {
    accordionHtml = `<div class="empty-hint">正在加载 Antigravity 会话历史...</div>`;
  } else if (sessions.length === 0) {
    accordionHtml = `<div class="empty-card">当前搜索或筛选条件下没有匹配的 Antigravity 子 Agent 记录。</div>`;
  } else {
    accordionHtml = sessions
      .map((sess) => {
        const isOpen = st.openSessionIds.has(sess.session_id);
        const runs = sess.runs || [];
        const actCount =
          typeof sess.active_count === "number"
            ? sess.active_count
            : runs.filter((r) => !r.closed).length;
        const closedCount =
          typeof sess.closed_count === "number"
            ? sess.closed_count
            : Math.max(0, runs.length - actCount);
        const issueCount = runs.filter(
          (r) =>
            r.status === "error" ||
            r.status === "blocked" ||
            r.status === "partial" ||
            r.status === "cancelled",
        ).length;
        const updatedLabel = formatTimestamp(sess.updated_at);
        const metaParts = [sess.workspace || "", updatedLabel !== "-" ? updatedLabel : ""].filter(
          Boolean,
        );
        const sessionTitle =
          sess.title && sess.title !== "default" ? sess.title : "未关联大会话 (default)";
        const subParts = [
          actCount > 0 ? `${actCount} 活跃` : "",
          `${closedCount} 归档`,
          issueCount > 0 ? `${issueCount} 异常` : "",
        ].filter(Boolean);

        const itemsHtml = runs
          .map((r) => {
            const isSel = selectedRun?.run_id === r.run_id;
            const role = getRunRole(r);
            const statusMeta = getRunStatusMeta(r.status);
            const showStatusBadge =
              r.status === "running" ||
              r.status === "partial" ||
              r.status === "error" ||
              r.status === "blocked" ||
              r.status === "cancelled";
            const title = getRunDisplayTitle(r);
            const digest = getRunWorkDigest(r);
            const ttlText = formatTtlRemaining(r, retentionHours);
            const stateFullLabel = !r.closed
              ? `活跃中 | ${ttlText}`
              : `${r.closed_by === "agent" ? "Agent 已关闭" : "已归档"} | ${ttlText}`;
            const ttlChip = !r.closed ? (r.status === "running" ? "运行中" : "活跃中") : ttlText;
            const archiveChip = r.closed
              ? `<span class="history-state-chip">${r.closed_by === "agent" ? "Agent 归档" : "手动归档"}</span>`
              : "";
            const model = r.observed?.model || r.model || r.effective?.model || "默认模型";
            const effort = r.observed?.effort || r.effort || r.effective?.effort || "默认";
            const elapsed =
              typeof r.duration_sec === "number"
                ? r.duration_sec
                : typeof r.elapsed_sec === "number"
                  ? r.elapsed_sec
                  : 0;
            const tokBadge = formatTokensBadge(r.usage, r.tokens_used);
            const metaLine = [model, effort, formatDuration(elapsed), tokBadge]
              .filter(Boolean)
              .join(" | ");

            return `<button type="button" data-hselect="${escapeHtml(r.run_id)}" class="history-run-item${isSel ? " is-selected" : ""}">
              <div class="history-run-head">
                <div class="history-run-head-left">
                  <span class="${statusDotClass(r.status)}" aria-hidden="true"></span>
                  <span class="history-run-title" title="${escapeHtml(title)}">${escapeHtml(title)}</span>
                </div>
                <span class="history-run-ttl" title="${escapeHtml(stateFullLabel)}">${escapeHtml(ttlChip)}</span>
              </div>
              <div class="history-run-badges">
                <span class="badge badge-info badge-nowrap" title="${escapeHtml(role)}">${escapeHtml(role)}</span>
                ${showStatusBadge ? `<span class="badge ${statusMeta.badgeClass} badge-nowrap">${escapeHtml(statusMeta.label)}</span>` : ""}
                ${archiveChip}
              </div>
              <div class="history-run-digest">${escapeHtml(digest)}</div>
              <div class="history-run-meta" title="${escapeHtml(metaLine)}">${escapeHtml(metaLine)}</div>
            </button>`;
          })
          .join("");

        return `<details class="session-group" data-session-id="${escapeHtml(sess.session_id)}"${isOpen ? " open" : ""}>
          <summary>
            <div class="session-summary-main">
              <div class="session-summary-left">
                <span class="session-title" title="${escapeHtml(sessionTitle)}">${escapeHtml(sessionTitle)}</span>
                ${metaParts.length ? `<span class="session-sub">${escapeHtml(metaParts.join(" | "))}</span>` : ""}
              </div>
              <div class="session-summary-right">
                <span class="badge badge-nowrap">${runs.length} 个子 Agent</span>
                <span class="session-count-sub${issueCount > 0 ? " is-warn" : ""}">${escapeHtml(subParts.join(" | "))}</span>
              </div>
            </div>
          </summary>
          <div class="session-children">${itemsHtml}</div>
        </details>`;
      })
      .join("");
  }

  const detailInnerHtml = selectedRun
    ? `<div class="history-detail-trace">${renderTraceView(selectedRun, st.traceState, { readOnly: true })}</div>`
    : `<div class="empty-hint" style="padding:18px">点击左侧任意 Antigravity 会话下的子 Agent，查看它执行的具体任务和日志。</div>`;

  return `<div class="history-view${st.inspecting ? " is-inspecting" : ""}">
    ${toolbarHtml}
    <div class="history-master-detail">
      <div class="history-list-col">
        <section id="session-accordion" class="session-accordion custom-scroll" aria-label="按 Antigravity 大会话分组的 Codex 子 Agent 记录">
          ${accordionHtml}
        </section>
      </div>
      <div class="history-detail-col">
        <div class="history-back-bar">
          <button type="button" id="history-back-list" class="link-btn">← 返回会话列表</button>
          <span class="trace-muted">子 Agent 只读审计</span>
        </div>
        ${detailInnerHtml}
      </div>
    </div>
  </div>`;
}
