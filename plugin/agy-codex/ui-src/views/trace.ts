import type { CommandEvent, ErrorEvent, FileChangeEvent, RunSnapshot, TimelineEvent } from "../types";
import {
  ICONS,
  buildTimelineBlocks,
  classifyCommand,
  escapeHtml,
  formatDuration,
  formatInlineMarkdown,
  formatTokensBadge,
  formatTtlRemaining,
  getRunRole,
  getRunStatusMeta,
  shortFileName,
  statusDotClass,
  summarizeGroup,
} from "../utils";

export interface TraceViewState {
  timelineEntries: TimelineEvent[];
  timelineRunId: string | null;
  expandedCmdIds: Set<string>;
  groupOpenOverrides: Map<string, boolean>;
  traceExpandedOverrides: Map<string, boolean>;
  promptExpandedIds: Set<string>;
  provenanceExpandedIds: Set<string>;
  composerDrafts: Map<string, string>;
  followupSending: boolean;
  userPinnedToBottom: boolean;
  canSendMessage: boolean;
  retentionHours: number;
  actionFeedback: string | null;
}

export interface TraceRenderOptions {
  readOnly?: boolean;
}

function renderKindTag(kind: string): string {
  const lower = kind.toLowerCase();
  if (lower === "add" || lower === "create") return `<span class="kind-add">+</span>`;
  if (lower === "delete" || lower === "remove") return `<span class="kind-delete">-</span>`;
  return `<span class="kind-update">~</span>`;
}

export function renderStepEvent(
  ev: TimelineEvent,
  idx: number,
  cwd: string,
  expandedCmdIds: Set<string>,
): string {
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
      ? `<span class="status-dot is-running" aria-hidden="true"></span>`
      : "";
    const labelClass = `step-label${cls.isProseLabel ? " is-prose-label" : ""}`;
    const outputBody = cmdEv.output
      ? `$ ${cmdEv.command}\n\n${cmdEv.output}`
      : `$ ${cmdEv.command}`;
    const outputHtml = expanded
      ? `<pre class="step-output">${escapeHtml(outputBody)}</pre>`
      : "";

    return `<div class="step-item">
      <button type="button" class="${rowClass}" data-toggle-cmd="${escapeHtml(id)}" title="${escapeHtml(cmdEv.command)}">
        ${leadIcon}${runningDot}<span class="${labelClass}">${escapeHtml(cls.label)}</span>
        ${expanded ? ICONS.chevronDown : ICONS.chevron}
      </button>
      ${outputHtml}
    </div>`;
  }

  if (ev.kind === "file_change") {
    const fcEv = ev as FileChangeEvent;
    const chips = (fcEv.changes || [])
      .map((c) => {
        const name = shortFileName(c.path, cwd);
        return `<button type="button" class="file-link" data-open-file="${escapeHtml(c.path)}" title="在侧边栏打开 ${escapeHtml(c.path)}">${renderKindTag(c.kind)}<span>${escapeHtml(name)}</span></button>`;
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

export function renderHandoffFlat(run: RunSnapshot): string {
  const res = run.result;
  if (!res || run.status === "running") return "";

  if (typeof res === "string") {
    return `<div class="handoff-flat">
      <div class="handoff-section-label">结果交接</div>
      <div class="handoff-summary prose-inline-code">${formatInlineMarkdown(res)}</div>
    </div>`;
  }

  const summaryHtml = typeof res.summary === "string" && res.summary
    ? `<div class="handoff-summary prose-inline-code">${formatInlineMarkdown(res.summary)}</div>`
    : "";

  const files = Array.isArray(res.changed_files) ? res.changed_files.filter((f) => typeof f === "string" && f.trim()) : [];
  const filesHtml =
    files.length > 0
      ? `<div class="file-row">${files
          .map(
            (f) =>
              `<button type="button" class="file-link" data-open-file="${escapeHtml(f)}" title="在侧边栏打开 ${escapeHtml(f)}"><span class="kind-update">~</span><span>${escapeHtml(shortFileName(f, run.cwd || "."))}</span></button>`,
          )
          .join("")}</div>`
      : "";

  const checks = Array.isArray(res.verification)
    ? res.verification.filter((v) => v && typeof v === "object" && !Array.isArray(v) &&
        typeof v.command === "string" && typeof v.passed === "boolean")
    : [];
  const verifyHtml =
    checks.length > 0
      ? `<div class="verify-list">${checks
          .map((v) => {
            const cls = v.passed ? "exit-ok" : "exit-fail";
            const label = v.passed ? "PASS" : "FAIL";
            const notes = typeof v.notes === "string" && v.notes ? `<span class="verify-note">${escapeHtml(v.notes)}</span>` : "";
            return `<div class="verify-item"><span class="exit-badge ${cls}">${label}</span><code class="verify-cmd">${escapeHtml(v.command)}</code>${notes}</div>`;
          })
          .join("")}</div>`
      : "";

  const risks = Array.isArray(res.risks) ? res.risks.filter((r) => typeof r === "string" && r.trim()) : [];
  const risksHtml =
    risks.length > 0
      ? `<div class="risks-box">${risks
          .map((r) => `<p class="risks-item">${escapeHtml(r)}</p>`)
          .join("")}</div>`
      : "";

  if (!summaryHtml && !verifyHtml && !filesHtml && !risksHtml) return "";

  return `<div class="handoff-flat">
    <div class="handoff-section-label">结果交接</div>
    ${summaryHtml}
    ${filesHtml}
    ${verifyHtml}
    ${risksHtml}
  </div>`;
}

export function renderTraceView(
  run: RunSnapshot,
  st: TraceViewState,
  opts: TraceRenderOptions = {},
): string {
  const readOnly = Boolean(opts.readOnly);
  const elapsedSec =
    typeof run.duration_sec === "number"
      ? run.duration_sec
      : typeof run.elapsed_sec === "number"
        ? run.elapsed_sec
        : 0;

  const roleText = getRunRole(run);
  const statusMeta = getRunStatusMeta(run.status);
  const modelLabel = run.observed?.model || run.model || run.effective?.model || "默认模型";
  const effortLabel = run.observed?.effort || run.effort || run.effective?.effort || "";
  const tokenText = formatTokensBadge(run.usage, run.tokens_used);
  const ttlText = formatTtlRemaining(run, st.retentionHours);
  const provOpen = st.provenanceExpandedIds.has(run.run_id);
  const promptOpen = st.promptExpandedIds.has(run.run_id);

  const remTimeoutSec =
    run.status === "running" && typeof run.timeout_sec === "number" && run.timeout_sec > 0
      ? Math.max(0, run.remaining_timeout_sec ?? run.timeout_sec - elapsedSec)
      : null;

  // Auto-collapse execution rail when completed cleanly; auto-expand when running/blocked/error/partial
  const defaultTraceOpen =
    run.status === "running" ||
    run.status === "partial" ||
    run.status === "blocked" ||
    run.status === "error" ||
    run.status === "cancelled";
  const traceOpen = st.traceExpandedOverrides.has(run.run_id)
    ? st.traceExpandedOverrides.get(run.run_id)!
    : defaultTraceOpen;

  const closeStateLabel = run.closed
    ? run.closed_by === "agent"
      ? "Agent 已关闭"
      : "已归档"
    : "活跃中";

  const hasObserved = Boolean(run.observed?.model || run.observed?.effort);
  const observedText = hasObserved
    ? `${run.observed?.model || "未知"} / ${run.observed?.effort || "未知"}`
    : "CLI --json 未回传";

  const reqText = `${run.requested?.model || "默认"} / ${run.requested?.effort || "默认"}`;
  const effText = `${run.effective?.model || run.model || "默认"} / ${run.effective?.effort || run.effort || "默认"}`;

  const provenanceHtml = provOpen
    ? `<div class="prov-drawer">
        <div>所属大会话: <span class="prov-val">${escapeHtml(run.parent_session_title || run.parent_session_id || "当前会话")}</span></div>
        <div>运行 ID: <code>${escapeHtml(run.run_id)}</code></div>
        <div>请求参数: <span class="prov-val">${escapeHtml(reqText)}</span></div>
        <div>实际生效: <span class="prov-val">${escapeHtml(effText)}</span></div>
        <div>实测模型: <span class="prov-val">${escapeHtml(observedText)}</span></div>
        <div>会话模式: <span class="prov-val">${run.persist ? "持久化恢复模式 (persist=true)" : "--ephemeral --thread-source subagent"}</span></div>
        <div>权限沙箱: <span class="prov-val">${escapeHtml(run.mode)} (${escapeHtml(run.config_mode || "isolated")})</span></div>
        <div>留存策略: <span class="prov-val">${escapeHtml(ttlText)}</span></div>
        ${
          typeof run.timeout_sec === "number" && run.timeout_sec > 0
            ? `<div>超时限制: <span class="prov-val">${escapeHtml(formatDuration(run.timeout_sec))}${remTimeoutSec !== null ? ` (<span${!readOnly ? ' id="timeout-remaining-hint"' : ""}>超时剩余 ${escapeHtml(formatDuration(remTimeoutSec))}</span>)` : ""}</span></div>`
            : ""
        }
        ${
          run.worktree?.cleanup_command
            ? `<div class="prov-full">Worktree 清理: <button type="button" class="btn btn-sm" data-copy="${escapeHtml(run.worktree.cleanup_command)}">复制清理命令</button></div>`
            : ""
        }
      </div>`
    : "";

  const ctxBarHtml = `<div class="trace-ctx-bar">
    <div class="trace-ctx-row">
      <div class="trace-ctx-left">
        <span class="${statusDotClass(run.status)}" aria-hidden="true"></span>
        ${run.status !== "running" ? `<span class="badge ${statusMeta.badgeClass}">${escapeHtml(statusMeta.label)}</span>` : ""}
        <span class="badge badge-info">${escapeHtml(roleText)}</span>
        <span class="trace-model-name">${escapeHtml(modelLabel)}</span>
        ${effortLabel ? `<span class="trace-muted">${escapeHtml(effortLabel)}</span>` : ""}
        ${
          run.worktree?.branch
            ? `<button type="button" class="wt-chip" data-copy="${escapeHtml(run.worktree.branch)}" title="点击复制 Worktree 分支">wt: ${escapeHtml(run.worktree.branch)}</button>`
            : ""
        }
        ${
          readOnly
            ? `<span class="badge">${escapeHtml(closeStateLabel)} | ${escapeHtml(ttlText)}</span>`
            : ""
        }
      </div>
      <div class="trace-ctx-right">
        <span${!readOnly && run.status === "running" ? ' id="elapsed-timer"' : ""} class="tabular-nums">${run.status === "running" ? "已运行 " : ""}${escapeHtml(formatDuration(elapsedSec))}</span>
        ${tokenText ? `<span class="tabular-nums">${escapeHtml(tokenText)}</span>` : ""}
        <button type="button" class="btn btn-sm" data-toggle-prov="${escapeHtml(run.run_id)}">${provOpen ? "收起详情" : "详情"}</button>
      </div>
    </div>
    ${run.status === "running" && run.in_flight_command
      ? `<div class="trace-in-flight trace-muted" title="${escapeHtml(run.in_flight_command)}">正在执行: <code>${escapeHtml(run.in_flight_command)}</code></div>`
      : ""}
    ${provenanceHtml}
  </div>`;

  const healthLabel = run.health === "stalled" ? "输出停滞" : run.health === "loop_warning" ? "疑似循环" : "";
  const healthBannerHtml = healthLabel
    ? `<div class="health-alert-banner"><strong>${healthLabel}</strong>${run.health_reason ? `<span>${escapeHtml(run.health_reason)}</span>` : ""}
        ${run.status === "running" ? `<span>预警本身不会停止任务；可继续观察，或${readOnly ? "前往活跃监控停止" : "点击底部「停止」"}。</span>` : ""}
      </div>`
    : "";
  const recentErrors = run.status === "running" && Array.isArray(run.recent_errors)
    ? run.recent_errors.filter((error) => typeof error === "string" && error.trim()).slice(-2)
    : [];
  const recentErrorsHtml = recentErrors.length
    ? `<div class="health-alert-banner is-error"><strong>最近报错</strong><ul>${recentErrors.map((error) => `<li title="${escapeHtml(error)}">${escapeHtml(error)}</li>`).join("")}</ul></div>`
    : "";

  const warnHtml =
    Array.isArray(run.warnings) && run.warnings.length > 0
      ? `<div class="banner-warn">${run.warnings.map((w) => escapeHtml(w)).join("<br/>")}</div>`
      : "";

  const promptBodyText = run.prompt || run.prompt_preview || "";
  const promptCardHtml = promptBodyText
    ? `<div class="prompt-card">
        <div class="prompt-card-head">
          <span class="prompt-card-title">派发任务指令</span>
          <button type="button" class="link-btn" data-toggle-prompt="${escapeHtml(run.run_id)}">${promptOpen ? "收起" : "展开全文"}</button>
        </div>
        <div class="prompt-card-body${promptOpen ? " is-expanded" : ""}">${escapeHtml(promptBodyText)}</div>
      </div>`
    : "";

  // 只读详情切换时，上一任务的时间线和展开状态不可用于新任务。
  const timelineMatches = !readOnly || st.timelineRunId === run.run_id;
  const events = timelineMatches ? st.timelineEntries : (run.events ?? []).filter((ev) => ev.kind !== "session" && ev.kind !== "turn_done");
  const timelineLoading = !timelineMatches && !Array.isArray(run.events) && !run.expired;
  const expandedCmdIds = timelineMatches ? st.expandedCmdIds : new Set<string>();
  const blocks = buildTimelineBlocks(events);
  let lastGroupIdx = -1;
  for (let i = blocks.length - 1; i >= 0; i--) {
    if (blocks[i].type === "group") {
      lastGroupIdx = i;
      break;
    }
  }

  const stepCount = events.filter((e) => e.kind !== "session" && e.kind !== "turn_done").length;
  const railHeading =
    run.status === "running"
      ? `执行中 ${formatDuration(elapsedSec)} | ${stepCount} 个步骤`
      : `用时 ${formatDuration(elapsedSec)} | ${stepCount} 个执行步骤`;

  const railContentHtml =
    blocks.length === 0
      ? `<div class="empty-hint">${
          run.expired
            ? "详细过程日志已超过留存期自动清理，下方保留永久摘要。"
            : run.status === "running"
              ? "正在等待 Codex 输出..."
              : run.error
                ? escapeHtml(run.error)
                : "暂无中间步骤记录。"
        }</div>`
      : blocks
          .map((blk, bIdx) => {
            if (blk.type === "commentary") {
              return `<div class="commentary prose-inline-code">${formatInlineMarkdown(blk.text)}</div>`;
            }
            const { icon, title, hasRunning } = summarizeGroup(blk.events);
            const isLastGroup = bIdx === lastGroupIdx;
            const defaultOpen = hasRunning || isLastGroup || blk.events.length <= 4;
            const isOpen = timelineMatches && st.groupOpenOverrides.has(blk.id)
              ? st.groupOpenOverrides.get(blk.id)!
              : defaultOpen;
            const innerHtml = isOpen
              ? `<div class="group-rail">${blk.events
                  .map((ev, eIdx) => renderStepEvent(ev, eIdx, run.cwd || ".", expandedCmdIds))
                  .join("")}</div>`
              : "";
            return `<div class="group">
              <button type="button" class="group-header" data-toggle-group="${escapeHtml(blk.id)}" data-current-open="${isOpen ? "1" : "0"}">
                ${icon}<span class="group-header-label">${escapeHtml(title)}</span>${isOpen ? ICONS.chevronDown : ICONS.chevron}
              </button>
              ${innerHtml}
            </div>`;
          })
          .join("");

  const execRailHtml =
    timelineLoading
      ? `<div class="empty-hint">正在加载此任务的执行日志...</div>`
      : stepCount > 0 || run.status === "running" || run.expired
      ? `<div class="exec-rail">
          <button type="button" class="rail-toggle-btn" data-toggle-trace="${escapeHtml(run.run_id)}">
            ${traceOpen ? ICONS.chevronDown : ICONS.chevron}
            <span>${escapeHtml(railHeading)}</span>
          </button>
          ${traceOpen ? `<div class="rail-body">${railContentHtml}</div>` : ""}
        </div>`
      : "";

  const errorBannerHtml =
    run.error && blocks.every((b) => b.type !== "group" || !b.events.some((e) => e.kind === "error"))
      ? `<div class="log-error">${escapeHtml(run.error)}</div>`
      : "";

  const handoffHtml = renderHandoffFlat(run);

  const feedbackHtml = st.actionFeedback
    ? `<div class="banner-ok">${escapeHtml(st.actionFeedback)}</div>`
    : "";

  // Footer
  let footerHtml = "";
  if (readOnly) {
    const statusDesc = run.expired
      ? "详细日志已到期回收，仅留永久摘要"
      : run.closed
        ? `${st.retentionHours} 小时追溯期内的只读归档`
        : "该子 Agent 当前仍在活跃标签栏";
    const actionBtn =
      run.closed && !run.expired
        ? `<button type="button" class="btn btn-primary btn-sm" data-reopen-run="${escapeHtml(run.run_id)}">恢复到活跃标签栏</button>`
        : !run.closed
          ? `<button type="button" class="btn btn-sm" data-jump-active="${escapeHtml(run.run_id)}">前往活跃标签</button>`
          : "";
    footerHtml = `<div class="trace-footer">
      <span class="trace-muted">${escapeHtml(statusDesc)}</span>
      ${actionBtn}
    </div>`;
  } else if (run.status === "running") {
    footerHtml = `<div class="trace-footer">
      <div class="trace-footer-status">
        <span class="status-dot is-running" aria-hidden="true"></span>
        <span id="running-footer-elapsed">已运行 ${escapeHtml(formatDuration(elapsedSec))}</span>
      </div>
      <button type="button" class="btn btn-danger btn-sm" id="stop-run-btn" data-stop-run="${escapeHtml(run.run_id)}">停止</button>
    </div>`;
  } else if (run.persist && run.session_id) {
    const draft = st.composerDrafts.get(run.run_id) || "";
    footerHtml = `<form class="trace-footer-form" data-followup-form="${escapeHtml(run.run_id)}">
      <input type="text" data-followup-input="${escapeHtml(run.run_id)}" value="${escapeHtml(draft)}" placeholder="向此长驻子 Agent 发送追问 (persist=true)..." ${st.followupSending ? "disabled" : ""} />
      <button type="submit" class="btn btn-primary btn-sm" ${st.followupSending ? "disabled" : ""}>${st.followupSending ? "发送中..." : "发送"}</button>
      <button type="button" class="btn btn-sm" data-close-run="${escapeHtml(run.run_id)}">归档</button>
    </form>`;
  } else {
    footerHtml = `<div class="trace-footer">
      <span class="trace-muted">用完即销毁 (关闭留存${st.retentionHours}h)</span>
      <div class="trace-footer-actions">
        ${
          st.canSendMessage
            ? `<button type="button" class="btn btn-sm" id="send-review-msg-btn">发给主 Agent 复核</button>`
            : ""
        }
        <button type="button" class="btn btn-sm" data-close-run="${escapeHtml(run.run_id)}">关闭归档</button>
      </div>
    </div>`;
  }

  return `<div class="trace-shell" data-trace-run="${escapeHtml(run.run_id)}" data-trace-mode="${readOnly ? "history" : "monitor"}">
    ${ctxBarHtml}
    <div class="trace-scroll-wrap">
      <div class="trace-scroll custom-scroll" id="timeline-box">
        ${healthBannerHtml || recentErrorsHtml ? `<div class="trace-alerts">${healthBannerHtml}${recentErrorsHtml}</div>` : ""}
        ${warnHtml}
        ${feedbackHtml}
        ${promptCardHtml}
        ${execRailHtml}
        ${errorBannerHtml}
        ${handoffHtml}
      </div>
      ${
        !readOnly && run.status === "running"
          ? `<button type="button" class="jump-circle-btn" id="jump-latest-btn" title="回到底部" style="display:${!st.userPinnedToBottom ? "inline-flex" : "none"}">${ICONS.arrowDown}</button>`
          : ""
      }
    </div>
    ${footerHtml}
  </div>`;
}
