import type { CommandEvent, RunSnapshot, TimelineEvent, UsageInfo } from "./types";

export type SemanticKind = "read" | "list" | "search" | "exec";

export interface ClassifiedCmd {
  semantic: SemanticKind;
  label: string;
  isProseLabel: boolean;
}

export type TimelineBlock =
  | { type: "commentary"; text: string }
  | { type: "group"; id: string; events: TimelineEvent[] };

export const ICONS = {
  chevron: `<svg class="chev" width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5"/></svg>`,
  chevronDown: `<svg class="chev open" width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5"/></svg>`,
  terminal: `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="1.75" y="2.75" width="12.5" height="10.5" rx="2"/><path d="M4.75 6.25L7 8 4.75 9.75"/><path d="M8.5 9.75h2.75"/></svg>`,
  folder: `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 4.25A1.25 1.25 0 0 1 3.25 3h3l1.3 1.5h5.2A1.25 1.25 0 0 1 14 5.75v6A1.25 1.25 0 0 1 12.75 13h-9.5A1.25 1.25 0 0 1 2 11.75v-7.5Z"/></svg>`,
  doc: `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 2.25h5l3 3v8.5A1.25 1.25 0 0 1 10.75 15h-6.5A1.25 1.25 0 0 1 3 13.75v-10.25A1.25 1.25 0 0 1 4.25 2.25Z"/><path d="M9 2.25v3h3"/><path d="M5.5 8.5h5M5.5 11h3.5"/></svg>`,
  search: `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="7" cy="7" r="4.25"/><path d="M10.2 10.2L13.5 13.5"/></svg>`,
  fileEdit: `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9.5 3.25l3.25 3.25-6.75 6.75H2.75V10l6.75-6.75Z"/></svg>`,
  arrowDown: `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 3v9.5"/><path d="M4 8.75L8 12.75l4-4"/></svg>`,
  closeX: `<svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden="true"><path d="M3 3l6 6M9 3L3 9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`,
};

export function escapeHtml(text: string): string {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function formatInlineMarkdown(text: string): string {
  const escaped = escapeHtml(text);
  return escaped
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
}

export function formatDuration(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m ${rem}s`;
}

export function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 10_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toLocaleString();
}

export function formatBytes(bytes: number): string {
  const b = Math.max(0, Number(bytes || 0));
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / (1024 * 1024)).toFixed(2)} MB`;
}

export function formatTimestamp(ts?: number | string | null): string {
  if (typeof ts === "string") return ts || "-";
  if (!ts || ts <= 0) return "-";
  const d = new Date(ts * 1000);
  const pad = (v: number) => String(v).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function formatTokensCompact(usage?: UsageInfo | null, total?: number | null): string | null {
  if (usage) {
    const fmt = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
    const hitPct =
      usage.input_tokens > 0
        ? ` (${Math.round((usage.cached_input_tokens / usage.input_tokens) * 100)}%)`
        : "";
    return `${fmt(usage.input_tokens)} in | ${fmt(usage.cached_input_tokens)} cached${hitPct} | ${fmt(usage.output_tokens)} out`;
  }
  if (typeof total === "number" && total > 0) {
    return `${formatNumber(total)} tok`;
  }
  return null;
}

export function formatTokensBadge(usage?: UsageInfo | null, total?: number | null): string | null {
  if (usage) {
    const tot =
      typeof total === "number" && total > 0
        ? total
        : (usage.input_tokens || 0) + (usage.output_tokens || 0);
    const hitPct =
      usage.input_tokens > 0
        ? Math.round((usage.cached_input_tokens / usage.input_tokens) * 100)
        : 0;
    return `${formatNumber(tot)} tok (${hitPct}% 缓存)`;
  }
  if (typeof total === "number" && total > 0) {
    return `${formatNumber(total)} tok`;
  }
  return null;
}

export function statusDotClass(status: string): string {
  if (status === "running") return "status-dot is-running";
  if (status === "done") return "status-dot is-done";
  if (status === "blocked" || status === "cancelled" || status === "partial") {
    return "status-dot is-blocked";
  }
  return "status-dot is-error";
}

export function getRunStatusMeta(status: string): { label: string; badgeClass: string } {
  if (status === "running") return { label: "执行中", badgeClass: "badge-info" };
  if (status === "done") return { label: "已完成", badgeClass: "badge-ok" };
  if (status === "partial") return { label: "部分完成", badgeClass: "badge-warn" };
  if (status === "blocked") return { label: "已受阻", badgeClass: "badge-warn" };
  if (status === "cancelled") return { label: "已停止", badgeClass: "badge-warn" };
  return { label: "执行失败", badgeClass: "badge-err" };
}

function stripTitleBoilerplate(raw: string): string {
  let s = raw.trim().replace(/\s+/g, " ");
  for (let i = 0; i < 4; i++) {
    const next = s
      .replace(/^\[[^\]]{1,40}\]\s*/, "")
      .replace(/^(?:任务|目标|task|goal)\s*[:：]\s*/i, "")
      .trim();
    if (next === s) break;
    s = next;
  }
  return s;
}

function capShortTitle(text: string, maxChars = 14): string {
  return text.length > maxChars ? `${text.slice(0, maxChars - 1)}…` : text;
}

export function getRunShortTitle(run: RunSnapshot): string {
  if (run.title && run.title.trim()) {
    const cleaned = stripTitleBoilerplate(run.title);
    return capShortTitle(cleaned || run.title.trim());
  }
  if (run.preset && run.preset.trim()) {
    return capShortTitle(run.preset.trim());
  }
  const rawPrev = (run.prompt_preview || run.prompt || "").trim().replace(/\s+/g, " ");
  if (rawPrev) {
    const cleaned = stripTitleBoilerplate(rawPrev);
    return capShortTitle(cleaned || rawPrev);
  }
  return run.run_id;
}

export function getRunDisplayTitle(run: RunSnapshot): string {
  if (run.title && run.title.trim()) {
    const cleaned = stripTitleBoilerplate(run.title);
    return capShortTitle(cleaned || run.title.trim(), 64);
  }
  if (run.preset && run.preset.trim()) {
    return capShortTitle(run.preset.trim(), 64);
  }
  const rawPrev = (run.prompt_preview || run.prompt || "").trim().replace(/\s+/g, " ");
  if (rawPrev) {
    const cleaned = stripTitleBoilerplate(rawPrev);
    return capShortTitle(cleaned || rawPrev, 64);
  }
  return run.run_id;
}

export function getRunRole(run: RunSnapshot): string {
  if (run.role && run.role.trim()) return run.role.trim();
  if (run.preset && run.preset.trim()) return run.preset.trim();
  return run.mode === "read-only" ? "只读探索" : "编码实现";
}

export function getRunWorkDigest(run: RunSnapshot): string {
  if (run.work_digest && run.work_digest.trim()) return run.work_digest.trim();
  if (run.result && typeof run.result === "object" && run.result.summary) {
    return run.result.summary;
  }
  if (typeof run.result === "string" && run.result.trim()) return run.result.trim();
  if (run.error) return run.error;
  return (run.prompt_preview || run.prompt || run.run_id).trim();
}

export function formatTtlRemaining(run: RunSnapshot, retentionHours = 24): string {
  if (run.expired) return `>${retentionHours}h 仅留摘要`;
  if (!run.closed) {
    if (run.status === "running") return "运行中";
    return run.persist ? `persist=true (关闭后留存 ${retentionHours}h)` : `关闭后留存 ${retentionHours}h`;
  }
  let remSec = typeof run.ttl_remaining_sec === "number" ? run.ttl_remaining_sec : null;
  if (remSec === null && typeof run.closed_at === "number" && run.closed_at > 0) {
    const expiresAt = run.closed_at + retentionHours * 3600;
    remSec = Math.max(0, expiresAt - Date.now() / 1000);
  }
  if (remSec === null) return `留存 ${retentionHours}h`;
  if (remSec <= 0) return "即将过期";
  const hrs = Math.floor(remSec / 3600);
  const mins = Math.floor((remSec % 3600) / 60);
  return `剩余 ${hrs}h ${mins}m`;
}

export function stripWinPrefix(p: string): string {
  if (p.startsWith("\\\\?\\UNC\\")) return "\\\\" + p.slice(8);
  if (p.startsWith("\\\\?\\")) return p.slice(4);
  return p;
}

export function shortFileName(filePath: string, cwd: string): string {
  const normFile = stripWinPrefix(filePath).replace(/\\/g, "/");
  const normCwd = stripWinPrefix(cwd).replace(/\\/g, "/").replace(/\/+$/, "");
  if (normCwd && normFile.startsWith(normCwd + "/")) {
    return normFile.slice(normCwd.length + 1);
  }
  return normFile;
}

export function classifyCommand(cmdEv: CommandEvent): ClassifiedCmd {
  const raw = (cmdEv.display_command || cmdEv.command || "").trim();
  const failed = typeof cmdEv.exit_code === "number" && cmdEv.exit_code !== 0;
  if (failed) {
    return { semantic: "exec", label: `Command failed (${raw})`, isProseLabel: true };
  }
  if (cmdEv.status === "in_progress" || cmdEv.exit_code === null) {
    return { semantic: "exec", label: raw, isProseLabel: false };
  }

  const trimmed = raw
    .replace(/^(?:pwd|cd\s+\S+|Set-Location\s+\S+)\s*(?:&&|;)\s*/i, "")
    .trim();

  if (!/[|>;]/.test(trimmed)) {
    const readMatch = trimmed.match(
      /^(?:cat|head|tail|nl|bat|Get-Content|type)\s+(?:-\S+\s+)*(.+)$/i,
    );
    if (readMatch) {
      const target = readMatch[1].trim();
      const parts = target.split(/\s+/);
      if (parts.length <= 2) {
        return { semantic: "read", label: `已读取 ${target}`, isProseLabel: true };
      }
      return {
        semantic: "read",
        label: `已读取 ${parts[0]} 等 ${parts.length} 个文件`,
        isProseLabel: true,
      };
    }
    if (/^sed\s+-n\s+/.test(trimmed)) {
      const parts = trimmed.split(/\s+/);
      const last = parts[parts.length - 1] || "";
      if (last && !last.startsWith("-")) {
        return { semantic: "read", label: `已读取 ${last}`, isProseLabel: true };
      }
    }
    if (/^(?:rg\s+--files|find\s+|ls\b|dir\b|tree\b|Get-ChildItem\b)/i.test(trimmed)) {
      return { semantic: "list", label: `已列出文件 (${raw})`, isProseLabel: true };
    }
    if (/^(?:rg|grep|Select-String|findstr)\s+/i.test(trimmed)) {
      return { semantic: "search", label: `已搜索 (${raw})`, isProseLabel: true };
    }
  }

  return { semantic: "exec", label: raw, isProseLabel: false };
}

export function buildTimelineBlocks(events: TimelineEvent[]): TimelineBlock[] {
  const blocks: TimelineBlock[] = [];
  let currentGroup: TimelineEvent[] = [];
  let groupCounter = 0;

  const flushGroup = () => {
    if (currentGroup.length === 0) return;
    groupCounter += 1;
    blocks.push({ type: "group", id: `grp_${groupCounter}`, events: currentGroup });
    currentGroup = [];
  };

  for (const ev of events) {
    if (ev.kind === "commentary") {
      flushGroup();
      const text = String((ev as { text?: unknown }).text || "").trim();
      if (text) blocks.push({ type: "commentary", text });
    } else {
      currentGroup.push(ev);
    }
  }
  flushGroup();
  return blocks;
}

export function summarizeGroup(
  events: TimelineEvent[],
): { icon: string; title: string; hasRunning: boolean } {
  let reads = 0;
  let lists = 0;
  let searches = 0;
  let execs = 0;
  let fileEdits = 0;
  let failures = 0;
  let hasRunning = false;

  for (const ev of events) {
    if (ev.kind === "command") {
      const cmdEv = ev as CommandEvent;
      if (cmdEv.status === "in_progress" || cmdEv.exit_code === null) hasRunning = true;
      else if (typeof cmdEv.exit_code === "number" && cmdEv.exit_code !== 0) failures += 1;

      const cls = classifyCommand(cmdEv);
      if (cls.semantic === "read") reads += 1;
      else if (cls.semantic === "list") lists += 1;
      else if (cls.semantic === "search") searches += 1;
      else execs += 1;
    } else if (ev.kind === "file_change") {
      fileEdits += ((ev as { changes?: unknown[] }).changes || []).length || 1;
    } else if (ev.kind === "error") {
      failures += 1;
    }
  }

  const exploreParts: string[] = [];
  if (reads > 0) exploreParts.push(`读取 ${reads} 个文件`);
  if (lists > 0) exploreParts.push(`列出目录 ${lists} 次`);
  if (searches > 0) exploreParts.push(`搜索 ${searches} 次`);

  const actionParts: string[] = [];
  if (exploreParts.length > 0) actionParts.push(exploreParts.join("、"));
  if (execs > 0) actionParts.push(`运行 ${execs} 条命令`);
  if (fileEdits > 0) actionParts.push(`变更 ${fileEdits} 个文件`);

  let icon = ICONS.terminal;
  if (fileEdits > 0 && execs === 0 && exploreParts.length === 0) icon = ICONS.fileEdit;
  else if (exploreParts.length > 0 && execs === 0 && fileEdits === 0) icon = ICONS.folder;

  let title = actionParts.join(" / ") || `${events.length} 项操作`;
  if (hasRunning) title = `正在执行 (${title})`;
  else if (exploreParts.length > 0 && execs === 0 && fileEdits === 0)
    title = `已探索 (${exploreParts.join(" / ")})`;
  if (failures > 0) title += ` | ${failures} 项失败`;

  return { icon, title, hasRunning };
}

export function renderDailySvgChart(
  daily: Array<{ date: string; tokens: number; cached_tokens: number; count: number }>,
): string {
  if (!daily.length) {
    return `<div class="empty-hint">该时间范围内暂无每日趋势数据。</div>`;
  }
  const slice = daily.slice(-14);
  const maxTok = Math.max(1, ...slice.map((d) => d.tokens));
  const w = 560;
  const h = 144;
  const padL = 12;
  const padR = 12;
  const padT = 12;
  const padB = 26;
  const innerW = w - padL - padR;
  const innerH = h - padT - padB;
  const slotW = innerW / slice.length;
  const barW = Math.max(10, Math.min(28, slotW * 0.56));

  const bars = slice
    .map((d, idx) => {
      const x = padL + idx * slotW + (slotW - barW) / 2;
      const totH = Math.max(2, (d.tokens / maxTok) * innerH);
      const totY = padT + innerH - totH;
      const cachedH =
        d.tokens > 0 ? Math.min(totH, (d.cached_tokens / d.tokens) * totH) : 0;
      const cachedY = padT + innerH - cachedH;
      const shortDate = d.date.length >= 10 ? d.date.slice(5) : d.date;
      const labelStep = slice.length > 8 ? 2 : 1;
      const labelHtml =
        idx % labelStep === 0 || idx === slice.length - 1
          ? `<text x="${(x + barW / 2).toFixed(1)}" y="${h - 6}" text-anchor="middle" font-size="12" fill="var(--color-text-tertiary, #6e7781)">${escapeHtml(shortDate)}</text>`
          : "";
      const tip = `${d.date}: ${d.count} 次派发 | 总用量 ${formatNumber(d.tokens)} tok | 缓存 ${formatNumber(d.cached_tokens)} tok`;
      return `<g>
        <title>${escapeHtml(tip)}</title>
        <rect x="${x.toFixed(1)}" y="${totY.toFixed(1)}" width="${barW.toFixed(1)}" height="${totH.toFixed(1)}" rx="3" fill="var(--color-text-info, #58a6ff)" fill-opacity="0.38"/>
        ${
          cachedH > 0.5
            ? `<rect x="${x.toFixed(1)}" y="${cachedY.toFixed(1)}" width="${barW.toFixed(1)}" height="${cachedH.toFixed(1)}" rx="3" fill="var(--color-text-success, #3fb950)" fill-opacity="0.86"/>`
            : ""
        }
        ${labelHtml}
      </g>`;
    })
    .join("");

  return `<svg viewBox="0 0 ${w} ${h}" class="trend-svg" role="img" aria-label="Token 消耗与缓存趋势图">
    <line x1="${padL}" y1="${padT + innerH}" x2="${w - padR}" y2="${padT + innerH}" stroke="var(--color-border-primary, rgba(128,128,128,0.25))" stroke-width="1"/>
    ${bars}
  </svg>`;
}

export function renderHelpTip(id: string, label: string, text: string): string {
  const tipId = `tip-${id}`;
  return `<span class="help-tip-wrap">
    <button type="button" class="help-tip-btn" interestfor="${escapeHtml(tipId)}" popovertarget="${escapeHtml(tipId)}" popovertargetaction="show" aria-expanded="false" aria-label="关于 ${escapeHtml(label)} 的说明">?</button>
    <div id="${escapeHtml(tipId)}" popover="hint" role="tooltip" class="help-tip-popover prose-inline-code">${formatInlineMarkdown(text)}</div>
  </span>`;
}

export function renderCodexToggleButton(
  enabled: boolean,
  variant: "topbar" | "inline" | "card" = "inline",
): string {
  const stateClass = enabled ? "is-on" : "is-off";
  const tip = enabled
    ? "点击关闭 Codex 子代理联动（改为 Antigravity 原生子代理执行）"
    : "点击开启 Codex 子代理联动（恢复向 Codex 模型派发子任务）";
  let labelHtml = "";
  if (variant === "topbar") {
    labelHtml = `<span class="codex-toggle-text">Codex 联动: ${enabled ? "已开启" : "已关闭"}</span><span class="codex-toggle-short">联动: ${enabled ? "开" : "关"}</span>`;
  } else if (variant === "card") {
    labelHtml = `<span class="codex-toggle-text">${enabled ? "已开启" : "已关闭"}</span><span class="codex-toggle-action">${enabled ? "点击关闭" : "点击开启"}</span>`;
  } else {
    labelHtml = `<span class="codex-toggle-text">Codex 联动: ${enabled ? "已开启" : "已关闭"}</span><span class="codex-toggle-action">${enabled ? "点击关闭" : "点击开启"}</span>`;
  }
  return `<button type="button" data-toggle-codex-master role="switch" aria-checked="${enabled ? "true" : "false"}" class="codex-toggle-btn is-${variant} ${stateClass}" title="${escapeHtml(tip)}">
    <span class="codex-toggle-dot" aria-hidden="true"></span>
    ${labelHtml}
    <span class="codex-toggle-track" aria-hidden="true"><span class="codex-toggle-thumb"></span></span>
  </button>`;
}
