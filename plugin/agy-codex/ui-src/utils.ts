import type { CommandEvent, TimelineEvent, UsageInfo } from "./types";

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
  chevron: `<svg class="chev" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.5L10.5 8 6 12.5"/></svg>`,
  chevronDown: `<svg class="chev open" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.5L10.5 8 6 12.5"/></svg>`,
  terminal: `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="1.75" y="2.75" width="12.5" height="10.5" rx="2"/><path d="M4.75 6.25L7 8 4.75 9.75"/><path d="M8.5 9.75h2.75"/></svg>`,
  folder: `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M2 4.25A1.25 1.25 0 0 1 3.25 3h3l1.3 1.5h5.2A1.25 1.25 0 0 1 14 5.75v6A1.25 1.25 0 0 1 12.75 13h-9.5A1.25 1.25 0 0 1 2 11.75v-7.5Z"/></svg>`,
  doc: `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 2.25h5l3 3v8.5A1.25 1.25 0 0 1 10.75 15h-6.5A1.25 1.25 0 0 1 3 13.75v-10.25A1.25 1.25 0 0 1 4.25 2.25Z"/><path d="M9 2.25v3h3"/><path d="M5.5 8.5h5M5.5 11h3.5"/></svg>`,
  search: `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="7" r="4.25"/><path d="M10.2 10.2L13.5 13.5"/></svg>`,
  fileEdit: `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 3.25l3.25 3.25-6.75 6.75H2.75V10l6.75-6.75Z"/></svg>`,
  arrowDown: `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3v9.5"/><path d="M4 8.75L8 12.75l4-4"/></svg>`,
};

export function escapeHtml(text: string): string {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
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

export function formatTimeAgo(ts?: number | null): string {
  if (!ts || ts <= 0) return "";
  const delta = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (delta < 60) return `${delta}s 前`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m 前`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h 前`;
  return `${Math.floor(delta / 86400)}d 前`;
}

export function formatTimestamp(ts?: number | null): string {
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
    return `${fmt(usage.input_tokens)} in · ${fmt(usage.cached_input_tokens)} cached${hitPct} · ${fmt(usage.output_tokens)} out`;
  }
  if (typeof total === "number" && total > 0) {
    return `${formatNumber(total)} tok`;
  }
  return null;
}

export function stripWinPrefix(p: string): string {
  if (p.startsWith("\\\\?\\UNC\\")) return "\\\\" + p.slice(8);
  if (p.startsWith("\\\\?\\")) return p.slice(4);
  return p;
}

export function toAbsFileUri(filePath: string, cwd: string): string {
  const clean = stripWinPrefix(filePath);
  if (clean.startsWith("file://")) return clean;
  let abs = clean;
  if (!clean.startsWith("/") && !/^[A-Za-z]:[\\/]/.test(clean)) {
    const base = stripWinPrefix(cwd).replace(/[\\/]+$/, "");
    abs = `${base}/${clean.replace(/^[\\/]+/, "")}`;
  }
  const normalized = abs.replace(/\\/g, "/");
  return normalized.startsWith("/") ? `file://${normalized}` : `file:///${normalized}`;
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

  // Strip common POSIX & PowerShell directory change prefixes
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
  if (failures > 0) title += ` · ${failures} 项失败`;

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
  const w = 520;
  const h = 132;
  const padL = 8;
  const padR = 8;
  const padT = 12;
  const padB = 22;
  const innerW = w - padL - padR;
  const innerH = h - padT - padB;
  const slotW = innerW / slice.length;
  const barW = Math.max(8, Math.min(24, slotW * 0.62));

  const bars = slice
    .map((d, idx) => {
      const x = padL + idx * slotW + (slotW - barW) / 2;
      const totH = Math.max(2, (d.tokens / maxTok) * innerH);
      const totY = padT + innerH - totH;
      const cachedH =
        d.tokens > 0 ? Math.min(totH, (d.cached_tokens / d.tokens) * totH) : 0;
      const cachedY = padT + innerH - cachedH;
      const shortDate = d.date.slice(5);
      const labelStep = slice.length > 9 ? 2 : 1;
      const labelHtml =
        idx % labelStep === 0 || idx === slice.length - 1
          ? `<text x="${x + barW / 2}" y="${h - 5}" text-anchor="middle" font-size="9.5" fill="var(--color-text-tertiary,#6e7781)">${escapeHtml(shortDate)}</text>`
          : "";
      const tip = `${d.date}: ${formatNumber(d.tokens)} tok (cached ${formatNumber(d.cached_tokens)}, ${d.count} runs)`;
      return `<g>
        <title>${escapeHtml(tip)}</title>
        <rect x="${x.toFixed(1)}" y="${totY.toFixed(1)}" width="${barW.toFixed(1)}" height="${totH.toFixed(1)}" rx="3" fill="var(--color-text-info,#58a6ff)" fill-opacity="0.42"/>
        ${
          cachedH > 0.5
            ? `<rect x="${x.toFixed(1)}" y="${cachedY.toFixed(1)}" width="${barW.toFixed(1)}" height="${cachedH.toFixed(1)}" rx="3" fill="var(--color-text-success,#3fb950)" fill-opacity="0.82"/>`
            : ""
        }
        ${labelHtml}
      </g>`;
    })
    .join("");

  return `<svg viewBox="0 0 ${w} ${h}" class="trend-svg" preserveAspectRatio="none">
    <line x1="${padL}" y1="${padT + innerH}" x2="${w - padR}" y2="${padT + innerH}" stroke="var(--color-border-primary,rgba(128,128,128,0.25))" stroke-width="1"/>
    ${bars}
  </svg>`;
}
