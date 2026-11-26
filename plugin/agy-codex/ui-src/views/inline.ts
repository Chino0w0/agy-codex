import type { CommandEvent, ErrorEvent, FileChangeEvent, RunSnapshot, TimelineEvent } from "../types";
import {
  ICONS,
  buildTimelineBlocks,
  classifyCommand,
  escapeHtml,
  formatDuration,
  formatInlineMarkdown,
  formatTokensCompact,
  shortFileName,
  summarizeGroup,
} from "../utils";

export interface TraceViewState {
  timelineEntries: TimelineEvent[];
  expandedCmdIds: Set<string>;
  groupOpenOverrides: Map<string, boolean>;
  traceExpanded: boolean;
  promptExpanded: boolean;
  userPinnedToBottom: boolean;
}

function renderKindTag(kind: string): string {
  const lower = kind.toLowerCase();
  if (lower === "add" || lower === "create") return `<span class="kind-add">+</span>`;
  if (lower === "delete" || lower === "remove") return `<span class="kind-delete">-</span>`;
  return `<span class="kind-update">M</span>`;
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
      ? `<span class="dot pulse" style="color:var(--color-text-info,#58a6ff)"></span>`
      : "";
    const labelClass = `step-label${cls.isProseLabel ? " is-prose-label" : ""}`;
    const outputBody = cmdEv.output
      ? `$ ${cmdEv.command}\n\n${cmdEv.output}`
      : `$ ${cmdEv.command}`;
    const outputHtml = expanded
      ? `<pre class="step-output">${escapeHtml(outputBody)}</pre>`
      : "";

    return `<div class="step-item">
      <button class="${rowClass}" data-toggle-cmd="${escapeHtml(id)}" title="${escapeHtml(cmdEv.command)}">
        ${leadIcon}${runningDot}<span class="${labelClass}">${escapeHtml(cls.label)}</span>
      </button>
      ${outputHtml}
    </div>`;
  }

  if (ev.kind === "file_change") {
    const fcEv = ev as FileChangeEvent;
    const chips = (fcEv.changes || [])
      .map((c) => {
        const name = shortFileName(c.path, cwd);
        return `<button class="file-link" data-open-file="${escapeHtml(c.path)}" title="在侧边栏打开 ${escapeHtml(c.path)}">${renderKindTag(c.kind)}<span>${escapeHtml(name)}</span></button>`;
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

export function renderHandoff(run: RunSnapshot): string {
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
              `<button class="file-link" data-open-file="${escapeHtml(f)}" title="在侧边栏打开">${escapeHtml(f)}</button>`,
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

export function renderRunTraceShell(
  run: RunSnapshot,
  st: TraceViewState,
  extraActionsHtml = "",
): string {
  const elapsedSec =
    typeof run.duration_sec === "number"
      ? run.duration_sec
      : typeof run.elapsed_sec === "number"
        ? run.elapsed_sec
        : 0;

  const statusLabel =
    run.status === "running"
      ? `运行中 ${formatDuration(elapsedSec)}`
      : run.status === "done"
        ? `用时 ${formatDuration(elapsedSec)}`
        : run.status === "cancelled"
          ? `已取消 (${formatDuration(elapsedSec)})`
          : `执行失败 (${formatDuration(elapsedSec)})`;

  const statusDot =
    run.status === "running"
      ? `<span class="dot pulse" style="color:var(--color-text-info,#58a6ff)"></span>`
      : "";

  const badges: string[] = [];
  if (run.source === "cli") badges.push(`<span class="badge">CLI</span>`);
  if (run.preset) badges.push(`<span class="badge badge-info">${escapeHtml(run.preset)}</span>`);
  const modelLabel = run.observed?.model || run.model || run.effective?.model;
  if (modelLabel) badges.push(`<span class="badge">${escapeHtml(modelLabel)}</span>`);
  if (run.provider) badges.push(`<span class="badge">${escapeHtml(run.provider)}</span>`);
  const eff = run.observed?.effort || run.effort || run.effective?.effort;
  if (eff) badges.push(`<span class="badge">${escapeHtml(eff)}</span>`);
  badges.push(`<span class="badge">${escapeHtml(run.mode)}</span>`);
  if (run.worktree?.branch) {
    badges.push(`<span class="badge badge-ok">${escapeHtml(run.worktree.branch)}</span>`);
  }
  const tokenText = formatTokensCompact(run.usage, run.tokens_used);
  if (tokenText) badges.push(`<span class="badge">${escapeHtml(tokenText)}</span>`);

  const stopBtn =
    run.status === "running"
      ? `<button class="btn btn-danger btn-sm" id="stop-run-btn">停止</button>`
      : "";

  const warnHtml =
    Array.isArray(run.warnings) && run.warnings.length > 0
      ? `<div class="banner-warn" style="margin-bottom:6px">${run.warnings.map((w) => escapeHtml(w)).join("<br/>")}</div>`
      : "";

  const identityLine =
    run.requested || run.observed
      ? `<div class="trace-meta" style="margin-bottom:4px">
          <span>请求: ${escapeHtml(run.requested?.model || "默认")}/${escapeHtml(run.requested?.effort || "默认")}</span>
          <span>·</span>
          <span>生效: ${escapeHtml(run.effective?.model || run.model || "默认")}/${escapeHtml(run.effective?.effort || run.effort || "默认")}</span>
          <span>·</span>
          <span>实测: ${escapeHtml(run.observed?.model || "未知")}/${escapeHtml(run.observed?.effort || "未知")}</span>
        </div>`
      : "";

  const promptHtml = run.prompt_preview
    ? `<div class="prompt-strip${st.promptExpanded ? " expanded" : ""}" id="prompt-toggle" title="点击展开/收起任务指令">${st.promptExpanded ? ICONS.chevronDown : ICONS.chevron}<span class="prompt-strip-text">${escapeHtml(run.prompt_preview)}</span></div>`
    : "";

  const blocks = buildTimelineBlocks(st.timelineEntries);
  let lastGroupIdx = -1;
  for (let i = blocks.length - 1; i >= 0; i--) {
    if (blocks[i].type === "group") {
      lastGroupIdx = i;
      break;
    }
  }

  const timelineHtml =
    blocks.length === 0
      ? `<div class="empty-hint">${run.status === "running" ? "正在等待 Codex 输出..." : run.error ? escapeHtml(run.error) : "暂无中间过程记录。"}</div>`
      : blocks
          .map((blk, bIdx) => {
            if (blk.type === "commentary") {
              return `<div class="commentary">${formatInlineMarkdown(blk.text)}</div>`;
            }
            const { icon, title, hasRunning } = summarizeGroup(blk.events);
            const isLastGroup = bIdx === lastGroupIdx;
            const defaultOpen = hasRunning || isLastGroup || blk.events.length <= 4;
            const isOpen = st.groupOpenOverrides.has(blk.id)
              ? st.groupOpenOverrides.get(blk.id)!
              : defaultOpen;
            const innerHtml = isOpen
              ? `<div class="group-rail">${blk.events
                  .map((ev, eIdx) => renderStepEvent(ev, eIdx, run.cwd, st.expandedCmdIds))
                  .join("")}</div>`
              : "";
            return `<div class="group">
              <button class="group-header" data-toggle-group="${escapeHtml(blk.id)}" data-current-open="${isOpen ? "1" : "0"}">
                ${icon}<span class="group-header-label">${escapeHtml(title)}</span>${isOpen ? ICONS.chevronDown : ICONS.chevron}
              </button>
              ${innerHtml}
            </div>`;
          })
          .join("");

  const traceBodyHtml = st.traceExpanded
    ? `<div class="timeline-wrap">
        <div class="timeline" id="timeline-box">${timelineHtml}</div>
        <button class="jump-circle-btn" id="jump-latest-btn" title="回到底部" style="display:${!st.userPinnedToBottom ? "inline-flex" : "none"}">${ICONS.arrowDown}</button>
      </div>`
    : "";

  return `<div class="shell">
    <div class="trace-header">
      <button class="trace-toggle${run.status === "error" ? " is-error" : ""}" id="trace-toggle-btn">
        ${statusDot}<span id="elapsed-timer">${escapeHtml(statusLabel)}</span>${st.traceExpanded ? ICONS.chevronDown : ICONS.chevron}
      </button>
      <div class="trace-meta">${badges.join("")}${stopBtn}</div>
    </div>
    ${identityLine}
    ${warnHtml}
    ${extraActionsHtml}
    ${promptHtml}
    ${traceBodyHtml}
    ${renderHandoff(run)}
  </div>`;
}
