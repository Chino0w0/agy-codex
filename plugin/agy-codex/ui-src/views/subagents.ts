import type { LocalSessionDetail, LocalSessionSummary, RunSnapshot } from "../types";
import {
  escapeHtml,
  formatDuration,
  formatNumber,
  formatTimeAgo,
  formatTimestamp,
  formatTokensCompact,
} from "../utils";
import { renderRunTraceShell, type TraceViewState } from "./inline";

export type SubagentFilter = "all" | "running" | "antigravity" | "codex_local";

export interface SubagentsViewState {
  filter: SubagentFilter;
  includeLocalSubagents: boolean;
  runs: RunSnapshot[];
  localSessions: LocalSessionSummary[];
  selectedType: "run" | "local";
  selectedId: string | null;
  currentRun: RunSnapshot | null;
  localDetail: LocalSessionDetail | null;
  traceState: TraceViewState;
  canSendMessage: boolean;
  canUpdateContext: boolean;
  actionFeedback: string | null;
}

function statusColor(status: string): string {
  if (status === "running") return "var(--color-text-info,#58a6ff)";
  if (status === "done") return "var(--color-text-success,#3fb950)";
  if (status === "cancelled" || status === "blocked") return "var(--color-text-warning,#d29922)";
  return "var(--color-text-danger,#f85149)";
}

function renderLocalSessionDetail(detail: LocalSessionDetail | null): string {
  if (!detail) {
    return `<div class="empty-hint">正在加载本机 Codex 会话详情...</div>`;
  }

  const usage = detail.usage;
  const lastU = detail.last_token_usage;
  const cw = detail.model_context_window;
  const pct = detail.context_percent;
  const barColor =
    typeof pct === "number" && pct >= 85
      ? "var(--color-text-danger,#f85149)"
      : typeof pct === "number" && pct >= 65
        ? "var(--color-text-warning,#d29922)"
        : "var(--color-text-info,#58a6ff)";

  const contextHtml =
    typeof pct === "number" && cw
      ? `<div class="section-card">
          <div class="section-title">
            <span>最近一轮上下文占用 (${pct}% · ${formatNumber(lastU?.input_tokens || 0)} / ${formatNumber(cw)})</span>
            ${detail.context_raw_ratio && detail.context_raw_ratio > 100 ? `<span class="badge badge-warn">原始占比 ${detail.context_raw_ratio}%</span>` : ""}
          </div>
          <div class="progress-track"><div class="progress-fill" style="width:${pct}%;background:${barColor}"></div></div>
        </div>`
      : "";

  const childrenHtml =
    detail.child_details && detail.child_details.length > 0
      ? `<div class="section-card">
          <div class="section-title"><span>Codex 内部派生子线程 (${detail.child_details.length})</span></div>
          ${detail.child_details
            .map(
              (c) => `<div style="display:flex;justify-content:space-between;gap:8px;padding:4px 0;border-bottom:1px solid var(--color-border-primary,rgba(128,128,128,0.12))">
                <div>
                  <span class="badge">${escapeHtml(c.thread_kind || "subagent")}</span>
                  <button class="btn btn-sm" style="margin-left:4px" data-select-local="${escapeHtml(c.thread_id)}">${escapeHtml(c.title)}</button>
                </div>
                <div class="trace-meta">
                  <span>${escapeHtml(c.model || "-")}</span>
                  <span>${formatNumber(c.tokens_used || 0)} tok</span>
                </div>
              </div>`,
            )
            .join("")}
        </div>`
      : "";

  const snippetsHtml =
    detail.snippets && detail.snippets.length > 0
      ? `<div class="section-card">
          <div class="section-title"><span>最近对话摘录</span></div>
          ${detail.snippets
            .map(
              (sn) => `<div class="snippet-item">
                <span class="badge ${sn.role === "user" ? "badge-info" : "badge-ok"}">${escapeHtml(sn.role)}</span>
                <div style="margin-top:4px;white-space:pre-wrap;word-break:break-word">${escapeHtml(sn.text)}</div>
              </div>`,
            )
            .join("")}
        </div>`
      : `<div class="empty-hint">该会话暂无可展示的 Rollout 文本摘录。</div>`;

  return `<div class="scroll-y">
    <div class="section-card">
      <div class="section-title">
        <span>${escapeHtml(detail.title || detail.thread_id)}</span>
        <span class="badge">${escapeHtml(detail.thread_kind)}</span>
      </div>
      <div class="trace-meta" style="margin-bottom:6px">
        ${detail.model ? `<span class="badge badge-info">${escapeHtml(detail.model)}</span>` : ""}
        ${detail.provider ? `<span class="badge">${escapeHtml(detail.provider)}</span>` : ""}
        ${detail.effort ? `<span class="badge">${escapeHtml(detail.effort)}</span>` : ""}
        ${detail.git_branch ? `<span class="badge badge-ok">${escapeHtml(detail.git_branch)}</span>` : ""}
        <span>总 Token: ${formatNumber(detail.tokens_used || 0)}</span>
        ${usage ? `<span>(输入 ${formatNumber(usage.input_tokens)} · 缓存 ${formatNumber(usage.cached_input_tokens)} · 输出 ${formatNumber(usage.output_tokens)})</span>` : ""}
      </div>
      <div style="font-size:11.5px;color:var(--color-text-tertiary,#6e7781)">
        目录: <code>${escapeHtml(detail.cwd || "-")}</code> · 更新: ${formatTimestamp(detail.updated_at)}
        ${detail.parent_thread_id ? ` · 父线程: <button class="btn btn-sm" data-select-local="${escapeHtml(detail.parent_thread_id)}">${escapeHtml(detail.parent_thread_id.slice(0, 8))}</button>` : ""}
      </div>
    </div>
    ${contextHtml}
    ${childrenHtml}
    ${snippetsHtml}
  </div>`;
}

export function renderSubagentsTab(st: SubagentsViewState): string {
  const filterBar = `<div class="filter-bar">
    ${(
      [
        ["all", "全部"],
        ["running", "运行中"],
        ["antigravity", "Antigravity 派发"],
        ["codex_local", "本机 Codex"],
      ] as const
    )
      .map(
        ([key, label]) =>
          `<button class="pill${st.filter === key ? " active" : ""}" data-sub-filter="${key}">${label}</button>`,
      )
      .join("")}
    ${
      st.filter === "all" || st.filter === "codex_local"
        ? `<label style="display:inline-flex;align-items:center;gap:4px;font-size:11.5px;color:var(--color-text-secondary,#8b949e);margin-left:auto;cursor:pointer">
            <input type="checkbox" id="toggle-local-subagents" ${st.includeLocalSubagents ? "checked" : ""}/>
            显示 Codex 内部子线程
          </label>`
        : ""
    }
  </div>`;

  interface ListRow {
    kind: "run" | "local";
    id: string;
    ts: number;
    html: string;
  }

  const rows: ListRow[] = [];

  if (st.filter !== "codex_local") {
    for (const r of st.runs) {
      if (st.filter === "running" && r.status !== "running") continue;
      const active = st.selectedType === "run" && st.selectedId === r.run_id ? " active" : "";
      const elapsed =
        typeof r.duration_sec === "number" ? r.duration_sec : Number(r.elapsed_sec || 0);
      const modelTxt = r.observed?.model || r.model || "默认模型";
      const srcTag = r.source === "cli" ? "CLI" : "MCP";
      const tokTxt = formatTokensCompact(r.usage, r.tokens_used);
      rows.push({
        kind: "run",
        id: r.run_id,
        ts: Number(r.started_at || 0),
        html: `<button class="run-card${active}" data-select-run="${escapeHtml(r.run_id)}">
          <div class="run-card-top">
            <span class="run-card-title">
              <span class="dot${r.status === "running" ? " pulse" : ""}" style="color:${statusColor(r.status)}"></span>
              <span>${escapeHtml(modelTxt)}</span>
            </span>
            <span class="badge">${srcTag}</span>
          </div>
          <div class="run-card-sub">
            ${r.preset ? `<span class="badge badge-info">${escapeHtml(r.preset)}</span>` : ""}
            ${r.effort ? `<span>${escapeHtml(r.effort)}</span>` : ""}
            ${r.worktree?.branch ? `<span>${escapeHtml(r.worktree.branch)}</span>` : ""}
            <span>${formatDuration(elapsed)}</span>
            ${tokTxt ? `<span>${escapeHtml(tokTxt)}</span>` : ""}
          </div>
          <div class="run-card-prompt">${escapeHtml(r.prompt_preview || r.run_id)}</div>
        </button>`,
      });
    }
  }

  if (st.filter === "all" || st.filter === "codex_local") {
    for (const s of st.localSessions) {
      if (!st.includeLocalSubagents && s.is_subagent) continue;
      const active =
        st.selectedType === "local" && st.selectedId === s.thread_id ? " active" : "";
      rows.push({
        kind: "local",
        id: s.thread_id,
        ts: Number(s.updated_at || s.created_at || 0),
        html: `<button class="run-card${active}" data-select-local="${escapeHtml(s.thread_id)}">
          <div class="run-card-top">
            <span class="run-card-title">
              <span class="dot" style="color:var(--color-text-secondary,#8b949e)"></span>
              <span>${escapeHtml(s.model || "Codex 会话")}</span>
            </span>
            <span class="badge">${escapeHtml(s.is_subagent ? s.thread_kind : "Codex")}</span>
          </div>
          <div class="run-card-sub">
            ${s.effort ? `<span>${escapeHtml(s.effort)}</span>` : ""}
            <span>${formatNumber(s.tokens_used || 0)} tok</span>
            <span>${escapeHtml(formatTimeAgo(s.updated_at || s.created_at))}</span>
          </div>
          <div class="run-card-prompt">${escapeHtml(s.title || s.cwd || s.thread_id)}</div>
        </button>`,
      });
    }
  }

  rows.sort((a, b) => b.ts - a.ts);

  const listHtml =
    rows.length === 0
      ? `<div class="empty-hint">暂无匹配的子 Agent 或会话记录。</div>`
      : rows.map((r) => r.html).join("");

  let detailHtml = `<div class="empty-hint">在左侧选择一个子 Agent 任务或本机 Codex 会话查看详情。</div>`;

  if (st.selectedType === "local") {
    detailHtml = renderLocalSessionDetail(st.localDetail);
  } else if (st.currentRun) {
    const r = st.currentRun;
    const actionBtns: string[] = [];
    if (r.worktree?.cleanup_command) {
      actionBtns.push(
        `<button class="btn btn-sm" id="copy-wt-cleanup" data-copy="${escapeHtml(r.worktree.cleanup_command)}">复制 Worktree 清理命令</button>`,
      );
    }
    if (st.canSendMessage && r.status !== "running") {
      actionBtns.push(`<button class="btn btn-primary btn-sm" id="send-review-msg-btn">发给主 Agent 复核</button>`);
    }
    if (st.canUpdateContext) {
      actionBtns.push(`<button class="btn btn-sm" id="attach-context-btn">附为下条消息上下文</button>`);
    }
    const actionBar =
      actionBtns.length > 0 || st.actionFeedback
        ? `<div class="action-row">${actionBtns.join("")}${
            st.actionFeedback
              ? `<span class="badge badge-ok">${escapeHtml(st.actionFeedback)}</span>`
              : ""
          }</div>`
        : "";
    detailHtml = renderRunTraceShell(r, st.traceState, actionBar);
  }

  return `<div class="subagents-layout">
    ${filterBar}
    <div class="split-grid">
      <div class="run-list">${listHtml}</div>
      <div class="detail-pane">${detailHtml}</div>
    </div>
  </div>`;
}
