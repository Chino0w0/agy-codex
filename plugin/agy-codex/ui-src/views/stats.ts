import type { StatsDimRow, StatsPayload } from "../types";
import {
  escapeHtml,
  formatBytes,
  formatDuration,
  formatNumber,
  formatTimestamp,
  renderCodexToggleButton,
  renderDailySvgChart,
} from "../utils";

export interface StatsViewState {
  range: "today" | "7d" | "30d" | "all";
  stats: StatsPayload | null;
  loading: boolean;
  loadError?: string | null;
  enabled: boolean;
}

function renderDimBreakdown(
  rows: StatsDimRow[],
  totalCount: number,
  emptyText: string,
  fillClass: string,
): string {
  if (!rows || rows.length === 0) {
    return `<div class="empty-hint">${escapeHtml(emptyText)}</div>`;
  }
  const denom = Math.max(1, totalCount, ...rows.map((r) => r.count));
  return `<div class="breakdown-list">
    ${rows
      .slice(0, 12)
      .map((row) => {
        const pct = Math.round((row.count / denom) * 100);
        const sRate =
          typeof row.success_rate === "number"
            ? `${row.success_rate}%`
            : row.count > 0
              ? `${Math.round(((row.count - (row.errors || 0)) / row.count) * 100)}%`
              : "-";
        const avgDur =
          row.avg_duration_sec !== null && row.avg_duration_sec !== undefined
            ? `均 ${formatDuration(row.avg_duration_sec)}`
            : "";
        const cacheTxt =
          row.cache_hit_rate !== null && row.cache_hit_rate !== undefined
            ? `缓存 ${row.cache_hit_rate}%`
            : "";
        const meta = [
          `${row.count} 次`,
          `成功 ${sRate}`,
          avgDur,
          cacheTxt,
          row.tokens > 0 ? `${formatNumber(row.tokens)} tok` : "",
        ]
          .filter(Boolean)
          .join(" | ");
        return `<div class="breakdown-item">
          <div class="breakdown-row-head">
            <span class="breakdown-name">${escapeHtml(row.name)}</span>
            <span class="breakdown-meta tabular-nums">${escapeHtml(meta)}</span>
          </div>
          <div class="progress-track">
            <div class="progress-fill ${fillClass}" style="width:${pct}%"></div>
          </div>
        </div>`;
      })
      .join("")}
  </div>`;
}

export function renderStatsView(st: StatsViewState): string {
  const ranges: Array<{ key: StatsViewState["range"]; label: string }> = [
    { key: "today", label: "今日" },
    { key: "7d", label: "近 7 天" },
    { key: "30d", label: "近 30 天" },
    { key: "all", label: "全部" },
  ];

  const headerHtml = `<div class="stats-header">
    <div class="trace-muted">仅统计 Antigravity 与 Codex 联动子 Agent 数据（永久账本 stats-ledger.jsonl，不含费用估算）</div>
    <div class="stats-header-right">
      ${renderCodexToggleButton(st.enabled, "inline")}
      <div class="seg-group" role="group" aria-label="统计周期">
        ${ranges
          .map(
            (rg) =>
              `<button type="button" class="seg-btn${st.range === rg.key ? " is-active" : ""}" data-stats-range="${rg.key}">${escapeHtml(rg.label)}</button>`,
          )
          .join("")}
      </div>
    </div>
  </div>`;

  if (!st.stats) {
    if (st.loadError) {
      return `<div class="scroll-y custom-scroll">
        <div class="page-stack">
          ${headerHtml}
          <div class="load-error-card">
            <div>加载统计数据失败: ${escapeHtml(st.loadError)}</div>
            <div class="load-error-actions">
              <button type="button" id="retry-stats-btn" class="btn btn-primary btn-sm">重试加载</button>
            </div>
          </div>
        </div>
      </div>`;
    }
    return `<div class="scroll-y custom-scroll">
      ${headerHtml}
      <div class="empty-hint">${st.loading ? "正在聚合联动统计数据..." : "暂无联动统计数据。"}</div>
    </div>`;
  }

  const s = st.stats;
  const ov = s.overview;
  const dispatchCount = ov.plugin_run_count ?? ov.total_items ?? 0;
  const issueCount = (ov.error_count || 0) + (ov.cancelled_count || 0);
  const sessionCount = ov.session_count ?? 0;
  const archive24h = ov.archive_24h_count ?? 0;
  const archiveBytes = ov.archive_storage_bytes ?? 0;

  const warnHtml =
    s.warnings && s.warnings.length > 0
      ? `<div class="banner-warn">${s.warnings.map((w) => escapeHtml(w)).join("<br/>")}</div>`
      : "";

  const kpiHtml = `<div class="stats-kpi-grid">
    <div class="kpi-card">
      <div class="kpi-label">累计联动派发</div>
      <div class="kpi-val tabular-nums">${dispatchCount} 次</div>
      <div class="kpi-sub">${sessionCount > 0 ? `跨越 ${sessionCount} 个 Antigravity 会话` : `进行中 ${ov.running_count} 个`}</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">任务成功率</div>
      <div class="kpi-val tabular-nums">${ov.success_rate !== null ? `${ov.success_rate}%` : "-"}</div>
      <div class="kpi-sub is-success">完成 ${ov.done_count} / 受阻异常 ${issueCount}</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">联动 Token 用量</div>
      <div class="kpi-val tabular-nums">${formatNumber(ov.total_tokens || 0)}</div>
      <div class="kpi-sub">平均缓存命中 ${ov.cache_hit_rate !== null ? `${ov.cache_hit_rate}%` : "-"}</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">24h 可追溯归档</div>
      <div class="kpi-val tabular-nums">${archive24h} 条可查</div>
      <div class="kpi-sub">临时日志 ${formatBytes(archiveBytes)}</div>
    </div>
  </div>`;

  const trendHtml = `<div class="section-card">
    <div class="section-title">
      <span>Token 消耗与缓存命中趋势</span>
      <div class="chart-legend">
        <span class="legend-item"><span class="legend-swatch is-info"></span>总输入/输出</span>
        <span class="legend-item"><span class="legend-swatch is-success"></span>缓存命中</span>
      </div>
    </div>
    ${renderDailySvgChart(s.daily)}
  </div>`;

  const breakdownHtml = `<div class="stats-breakdown-grid">
    <div class="section-card">
      <div class="section-title"><span>按 Codex 模型分布</span></div>
      ${renderDimBreakdown(s.by_model || [], dispatchCount, "暂无模型分布记录。", "is-info")}
    </div>
    <div class="section-card">
      <div class="section-title"><span>按子 Agent 角色分布</span></div>
      ${renderDimBreakdown(s.by_role || [], dispatchCount, "暂无角色分布记录。", "is-success")}
    </div>
  </div>`;

  const providerEffortHtml =
    (s.by_provider && s.by_provider.length > 0) || (s.by_effort && s.by_effort.length > 0)
      ? `<div class="section-card">
          <div class="section-title"><span>Provider 与推理档位分布</span></div>
          <div class="chip-row">
            <span class="trace-muted">Provider:</span>
            ${(s.by_provider || []).map((p) => `<span class="badge">${escapeHtml(p.name)}: ${p.count} (${formatNumber(p.tokens)})</span>`).join("") || `<span class="trace-muted">-</span>`}
          </div>
          <div class="chip-row" style="margin-top:6px">
            <span class="trace-muted">Effort:</span>
            ${(s.by_effort || []).map((e) => `<span class="badge badge-info">${escapeHtml(e.name)}: ${e.count} (${formatNumber(e.tokens)})</span>`).join("") || `<span class="trace-muted">-</span>`}
          </div>
        </div>`
      : "";

  const errHtml =
    s.recent_errors && s.recent_errors.length > 0
      ? `<div class="section-card">
          <div class="section-title"><span>最近报错 (${s.recent_errors.length})</span></div>
          <div class="error-list">
            ${s.recent_errors
              .slice(0, 8)
              .map(
                (er) => `<div class="snippet-item">
                  <div class="snippet-main">
                    <div class="snippet-meta">
                      <span class="badge badge-err">${escapeHtml(er.model)}</span>
                      <span class="trace-muted">${escapeHtml(formatTimestamp(er.ts))}</span>
                    </div>
                    <div class="snippet-err-msg">${escapeHtml(er.error)}</div>
                  </div>
                  <button type="button" class="btn btn-sm" data-copy="${escapeHtml(er.error)}">复制</button>
                </div>`,
              )
              .join("")}
          </div>
        </div>`
      : "";

  return `<div class="scroll-y custom-scroll">
    <div class="page-stack">
      ${headerHtml}
      ${warnHtml}
      ${kpiHtml}
      ${trendHtml}
      ${breakdownHtml}
      ${providerEffortHtml}
      ${errHtml}
    </div>
  </div>`;
}
