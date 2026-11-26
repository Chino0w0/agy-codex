import type { StatsPayload } from "../types";
import { escapeHtml, formatDuration, formatNumber, formatTimestamp, renderDailySvgChart } from "../utils";

export interface StatsViewState {
  scope: "all" | "antigravity" | "codex_local";
  range: "today" | "7d" | "30d" | "all";
  stats: StatsPayload | null;
  loading: boolean;
}

export function renderStatsTab(st: StatsViewState): string {
  const filterHtml = `<div class="filter-bar" style="margin-bottom:8px">
    <div style="display:flex;gap:5px;flex-wrap:wrap">
      ${(
        [
          ["all", "全部 Codex"],
          ["antigravity", "仅 Antigravity"],
          ["codex_local", "仅本机 Codex"],
        ] as const
      )
        .map(
          ([k, label]) =>
            `<button class="pill${st.scope === k ? " active" : ""}" data-stats-scope="${k}">${label}</button>`,
        )
        .join("")}
    </div>
    <div style="display:flex;gap:5px;flex-wrap:wrap;margin-left:auto">
      ${(
        [
          ["today", "今天"],
          ["7d", "近 7 天"],
          ["30d", "近 30 天"],
          ["all", "全部"],
        ] as const
      )
        .map(
          ([k, label]) =>
            `<button class="pill${st.range === k ? " active" : ""}" data-stats-range="${k}">${label}</button>`,
        )
        .join("")}
    </div>
  </div>`;

  if (!st.stats) {
    return `${filterHtml}<div class="empty-hint">${st.loading ? "正在聚合统计数据..." : "暂无统计数据。"}</div>`;
  }

  const s = st.stats;
  const ov = s.overview;
  const maxModelTok = Math.max(1, ...s.by_model.map((m) => m.tokens));

  const warnHtml =
    s.warnings && s.warnings.length > 0
      ? `<div class="banner-warn" style="margin-bottom:8px">${s.warnings.map((w) => escapeHtml(w)).join("<br/>")}</div>`
      : "";

  const kpiHtml = `<div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-label">总任务 / 会话</div>
      <div class="kpi-val">${ov.total_items}</div>
      <div class="kpi-sub">插件 ${ov.plugin_run_count} · 本地 ${ov.local_user_session_count} (+${ov.local_subagent_count} 子线程)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">累计 Token</div>
      <div class="kpi-val">${formatNumber(ov.total_tokens)}</div>
      <div class="kpi-sub">输入 ${formatNumber(ov.input_tokens)} · 输出 ${formatNumber(ov.output_tokens)}</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">缓存命中率</div>
      <div class="kpi-val">${ov.cache_hit_rate !== null ? `${ov.cache_hit_rate}%` : "-"}</div>
      <div class="kpi-sub">命中 ${formatNumber(ov.cached_input_tokens)} (${ov.cache_coverage_count} 样本)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">插件成功率 / 平均耗时</div>
      <div class="kpi-val">${ov.success_rate !== null ? `${ov.success_rate}%` : "-"}</div>
      <div class="kpi-sub">${ov.avg_duration_sec !== null ? `均值 ${formatDuration(ov.avg_duration_sec)}` : "仅插件任务口径"}</div>
    </div>
  </div>`;

  const trendHtml = `<div class="section-card">
    <div class="section-title">
      <span>每日 Token 趋势 (近 14 个活跃日)</span>
      <span class="trace-meta">蓝色=总输入/输出 · 绿色=缓存命中</span>
    </div>
    ${renderDailySvgChart(s.daily)}
  </div>`;

  const modelRows =
    s.by_model.length === 0
      ? `<div class="empty-hint">暂无模型分布记录。</div>`
      : `<table class="data-table">
          <thead>
            <tr>
              <th>模型</th>
              <th>次数</th>
              <th>Token 占比</th>
              <th>缓存命中</th>
              <th>失败</th>
            </tr>
          </thead>
          <tbody>
            ${s.by_model
              .slice(0, 15)
              .map((m) => {
                const pct = Math.round((m.tokens / maxModelTok) * 100);
                return `<tr>
                  <td><code>${escapeHtml(m.name)}</code></td>
                  <td>${m.count}</td>
                  <td style="min-width:120px">
                    <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:2px">
                      <span>${formatNumber(m.tokens)}</span>
                    </div>
                    <div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>
                  </td>
                  <td>${m.cache_hit_rate !== null ? `${m.cache_hit_rate}%` : "-"}</td>
                  <td>${m.errors > 0 ? `<span class="badge badge-err">${m.errors}</span>` : "0"}</td>
                </tr>`;
              })
              .join("")}
          </tbody>
        </table>`;

  const dimChips = `<div class="section-card">
    <div class="section-title"><span>Provider 与推理档位分布</span></div>
    <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px">
      <span class="trace-meta">Provider:</span>
      ${s.by_provider.map((p) => `<span class="badge">${escapeHtml(p.name)}: ${p.count} (${formatNumber(p.tokens)})</span>`).join("") || `<span class="trace-meta">-</span>`}
    </div>
    <div style="display:flex;flex-wrap:wrap;gap:6px">
      <span class="trace-meta">Effort:</span>
      ${s.by_effort.map((e) => `<span class="badge badge-info">${escapeHtml(e.name)}: ${e.count} (${formatNumber(e.tokens)})</span>`).join("") || `<span class="trace-meta">-</span>`}
    </div>
  </div>`;

  const errHtml =
    s.recent_errors.length > 0
      ? `<div class="section-card">
          <div class="section-title"><span>最近报错 (${s.recent_errors.length})</span></div>
          ${s.recent_errors
            .slice(0, 8)
            .map(
              (er) => `<div class="snippet-item" style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start">
                <div style="min-width:0;flex:1">
                  <div class="trace-meta">
                    <span class="badge badge-err">${escapeHtml(er.model)}</span>
                    <span>${formatTimestamp(er.ts)}</span>
                  </div>
                  <div style="margin-top:3px;color:var(--color-text-danger,#f85149);word-break:break-word">${escapeHtml(er.error)}</div>
                </div>
                <button class="btn btn-sm" data-copy="${escapeHtml(er.error)}">复制</button>
              </div>`,
            )
            .join("")}
        </div>`
      : "";

  return `<div class="scroll-y">
    ${filterHtml}
    ${warnHtml}
    ${kpiHtml}
    ${trendHtml}
    <div class="section-card">
      <div class="section-title"><span>按模型用量分布</span></div>
      ${modelRows}
    </div>
    ${dimChips}
    ${errHtml}
  </div>`;
}
