import type { CommandPreviewPayload, WorkbenchConfig, WorkbenchPayload } from "../types";
import { escapeHtml, formatNumber, renderCodexToggleButton, renderHelpTip } from "../utils";

export interface ModelsViewState {
  data: WorkbenchPayload | null;
  draft: WorkbenchConfig | null;
  preview: CommandPreviewPayload | null;
  searchQuery: string;
  loading?: boolean;
  loadError?: string | null;
  saving: boolean;
  enabled: boolean;
  excludeOrchestratorModels: boolean;
  retentionHours: number;
  statusMsg: string | null;
  errorMsg: string | null;
}

export function renderModelsView(st: ModelsViewState): string {
  if (!st.data || !st.draft) {
    if (st.loadError) {
      return `<div class="scroll-y custom-scroll config-view-root">
        <div class="page-stack">
          <div class="load-error-card">
            <div>加载模型目录与配置中心失败: ${escapeHtml(st.loadError)}</div>
            <div class="load-error-actions">
              <button type="button" id="retry-models-btn" class="btn btn-primary btn-sm">重试加载</button>
              ${renderCodexToggleButton(st.enabled, "inline")}
            </div>
          </div>
        </div>
      </div>`;
    }
    return `<div class="empty-hint">正在加载模型目录与配置中心...</div>`;
  }

  const d = st.data;
  const cfg = st.draft;
  const doc = d.doctor;
  const effDef = d.effective_defaults || {};
  const retentionHours =
    cfg.retention_hours ?? cfg.mapping.retention_hours ?? st.retentionHours ?? 24;

  const feedbackHtml = st.statusMsg
    ? `<div class="banner-ok">${escapeHtml(st.statusMsg)}</div>`
    : st.errorMsg
      ? `<div class="banner-warn">${escapeHtml(st.errorMsg)}</div>`
      : "";

  // Card 1: Instant Switch & Orchestrator Guard
  const switchCardHtml = `<div class="section-card">
    <div class="section-title">
      <span>Codex 子代理即时开关与编排保护</span>
      <div class="section-actions">
        <button type="button" class="btn btn-sm" id="preview-cmd-btn">预览命令行参数</button>
        <button type="button" class="btn btn-primary btn-sm" id="save-workbench-btn" ${st.saving ? "disabled" : ""}>${st.saving ? "保存中..." : "保存配置"}</button>
      </div>
    </div>
    <div class="switch-grid">
      <div class="switch-row-card ${st.enabled ? "is-on" : "is-off"}">
        <div class="switch-row-info">
          <div class="label-with-tip">
            <span class="switch-row-label">启用 Codex 子代理联动</span>
            ${renderHelpTip(
              "master-switch",
              "Codex 子代理联动开关",
              "关闭后立即暂停新的 Codex 子代理派发，所有子任务将直接交由 Antigravity 原生子 Agent 处理；需要调用 Codex 特定模型或隔离 Worktree 时可随时重新开启。",
            )}
          </div>
          <div class="switch-row-desc">${st.enabled ? "当前已开启：允许向 Codex 派发子任务" : "当前已暂停：由 Antigravity 原生子 Agent 接管"}</div>
        </div>
        ${renderCodexToggleButton(st.enabled, "card")}
      </div>

      <div class="switch-row-card">
        <div class="switch-row-info">
          <div class="label-with-tip">
            <label for="cfg-orch-switch" class="switch-row-label">排除 Orchestrator 模型派发</label>
            ${renderHelpTip(
              "orch-guard",
              "排除 Orchestrator 模型派发",
              "开启后提供双重防套娃保护：1) 自动从可选 Codex 子代理列表中排除 Orchestrator 编排类模型；2) 当 Antigravity 主会话处于编排模式时阻止嵌套派发。",
            )}
          </div>
          <div class="switch-row-desc">${st.excludeOrchestratorModels ? "已启用防嵌套派发保护" : "未限制 Orchestrator 派发"}</div>
        </div>
        <input id="cfg-orch-switch" type="checkbox" switch ${st.excludeOrchestratorModels ? "checked" : ""} aria-label="排除 Orchestrator 模型派发" />
      </div>
    </div>
  </div>`;

  // Card 2 & 3 Grid: Defaults/Retention + Forwarded MCP
  const providerOptions = [
    `<option value="">(继承 config.toml)</option>`,
    ...d.providers.map(
      (p) =>
        `<option value="${escapeHtml(p.id)}"${cfg.defaults.provider === p.id ? " selected" : ""}>${escapeHtml(p.id)} (${escapeHtml(p.name)})</option>`,
    ),
  ].join("");

  const activeEffModel = cfg.defaults.model || String(effDef.model || "");
  const matchedModelObj = d.models.find((m) => m.slug === activeEffModel);
  const effortChoices = Array.from(
    new Set([...(matchedModelObj?.supported_reasoning_levels || []), ...d.standard_efforts]),
  );

  const defaultsCardHtml = `<div class="section-card">
    <div class="section-title"><span>派发默认值与留存策略</span></div>
    <div class="form-stack">
      <div class="field">
        <label for="cfg-def-model">默认派发模型 (留空则派发前先让用户选定)</label>
        <input type="text" id="cfg-def-model" list="known-models-datalist" value="${escapeHtml(cfg.defaults.model || "")}" placeholder="派发前先让用户选定 (推荐留空)" />
        <datalist id="known-models-datalist">
          ${d.models
            .filter((m) => !(m.orchestrator_excluded && st.excludeOrchestratorModels))
            .map((m) => `<option value="${escapeHtml(m.slug)}">${escapeHtml(m.display_name)}</option>`)
            .join("")}
        </datalist>
      </div>

      <div class="form-row-2">
        <div class="field">
          <div class="label-with-tip">
            <label for="cfg-def-effort">推理档位 (effort)</label>
            ${renderHelpTip(
              "effort",
              "推理档位 effort",
              "控制 Codex 子代理思考深度：low 适合只读核查与简单脚本，medium 适合常规代码修改，high / xhigh 适合复杂根因排障；超出模型上限时会自动钳位并提示。",
            )}
          </div>
          <select id="cfg-def-effort">
            <option value="">(继承 config.toml)</option>
            ${effortChoices
              .map(
                (lv) =>
                  `<option value="${escapeHtml(lv)}"${cfg.defaults.effort === lv ? " selected" : ""}>${escapeHtml(lv)}</option>`,
              )
              .join("")}
          </select>
        </div>

        <div class="field">
          <label for="cfg-map-max-conc">最大并发槽位</label>
          <input type="number" id="cfg-map-max-conc" min="1" max="20" value="${cfg.mapping.max_concurrent_runs}" />
        </div>
      </div>

      <div class="form-row-2">
        <div class="field">
          <label for="cfg-def-provider">默认 Provider</label>
          <select id="cfg-def-provider">${providerOptions}</select>
        </div>

        <div class="field">
          <label for="cfg-map-retention-hours">归档日志留存 (小时)</label>
          <input type="number" id="cfg-map-retention-hours" min="1" max="168" value="${retentionHours}" />
        </div>
      </div>

      <div class="retention-box">
        <div class="label-with-tip">
          <span class="retention-title">临时销毁与 ${retentionHours}h 追溯机制</span>
          ${renderHelpTip(
            "ephemeral-ttl",
            "--ephemeral 与 24h 归档留存",
            "`--ephemeral --thread-source subagent` 保证调用结束后不在本地 Codex Desktop 留下垃圾会话；插件目录会为你留存指定小时数的完整步骤日志，过期后自动清理细节仅留 `stats-ledger.jsonl` 永久统计。",
          )}
        </div>
        <div class="trace-muted">底层启动参数: <code>--ephemeral --thread-source subagent</code> | 关闭后本地日志留存 ${retentionHours} 小时</div>
      </div>

      <details class="advanced-cfg">
        <summary>高级映射与隔离策略 (${escapeHtml(cfg.defaults.config_mode)} / ${escapeHtml(cfg.mapping.catalog_mode)})</summary>
        <div class="advanced-cfg-body">
          <div class="form-row-2">
            <div class="field">
              <label for="cfg-def-config-mode">配置隔离模式 (config_mode)</label>
              <select id="cfg-def-config-mode">
                <option value="isolated"${cfg.defaults.config_mode === "isolated" ? " selected" : ""}>isolated (白名单隔离, 推荐)</option>
                <option value="profile"${cfg.defaults.config_mode === "profile" ? " selected" : ""}>profile (-p worker)</option>
                <option value="inherit"${cfg.defaults.config_mode === "inherit" ? " selected" : ""}>inherit (继承完整 config.toml)</option>
              </select>
            </div>
            <div class="field">
              <label for="cfg-map-catalog-mode">Catalog 映射模式</label>
              <select id="cfg-map-catalog-mode">
                <option value="auto"${cfg.mapping.catalog_mode === "auto" ? " selected" : ""}>auto (自动透传)</option>
                <option value="custom"${cfg.mapping.catalog_mode === "custom" ? " selected" : ""}>custom (自定义路径)</option>
                <option value="off"${cfg.mapping.catalog_mode === "off" ? " selected" : ""}>off (禁用注入)</option>
              </select>
            </div>
            <div class="field">
              <label for="cfg-map-effort-policy">推理档位策略</label>
              <select id="cfg-map-effort-policy">
                <option value="clamp_with_warning"${cfg.mapping.effort_policy === "clamp_with_warning" ? " selected" : ""}>clamp_with_warning (钳位并警告)</option>
                <option value="passthrough"${cfg.mapping.effort_policy === "passthrough" ? " selected" : ""}>passthrough (原样透传)</option>
              </select>
            </div>
            <div class="field">
              <label for="cfg-map-cw-policy">上下文窗口透传</label>
              <select id="cfg-map-cw-policy">
                <option value="same_model_only"${cfg.mapping.context_window_policy === "same_model_only" ? " selected" : ""}>same_model_only (仅同模型)</option>
                <option value="always_forward"${cfg.mapping.context_window_policy === "always_forward" ? " selected" : ""}>always_forward (总是透传)</option>
                <option value="never_forward"${cfg.mapping.context_window_policy === "never_forward" ? " selected" : ""}>never_forward (不透传)</option>
              </select>
            </div>
          </div>
          <div class="field">
            <label for="cfg-map-catalog-path">自定义 Catalog 路径</label>
            <input type="text" id="cfg-map-catalog-path" value="${escapeHtml(cfg.mapping.catalog_path_override || "")}" placeholder="相对 CODEX_HOME 或绝对路径" />
          </div>
          <div class="field">
            <label for="cfg-map-extra-keys">额外白名单键 (逗号分隔)</label>
            <input type="text" id="cfg-map-extra-keys" value="${escapeHtml((cfg.mapping.extra_whitelist_keys || []).join(", "))}" placeholder="如 web_search, allow_login_shell" />
          </div>
          <label class="checkbox-inline">
            <input type="checkbox" id="cfg-map-allow-nested" ${cfg.mapping.allow_nested_subagents ? "checked" : ""} />
            <span>允许 Codex 内部派生子线程 (allow_nested_subagents)</span>
          </label>
        </div>
      </details>
    </div>
  </div>`;

  const fwdSet = new Set(cfg.mapping.forwarded_mcp_servers || []);
  const mcpList = d.mcp_servers || [];
  const mcpCardHtml = `<div class="section-card">
    <div class="section-title">
      <div class="label-with-tip">
        <span>按需透传 MCP 工具</span>
        ${renderHelpTip(
          "direct-ns",
          "透传 MCP 与 direct_only_tool_namespaces",
          "默认全关以防桌面端 MCP 阻塞。仅勾选的项目才会写入隔离配置并注入 `features.code_mode.direct_only_tool_namespaces`，使 Codex 子代理无需额外激活即可直连工具。",
        )}
      </div>
      <span class="trace-muted">按需勾选</span>
    </div>
    ${
      mcpList.length === 0
        ? `<div class="empty-hint">未检测到本地可透传的 Codex MCP 服务配置。</div>`
        : `<div class="mcp-list">
            ${mcpList
              .map((m) => {
                const isChecked = fwdSet.has(m.id);
                const hint = m.command || m.url || m.type;
                return `<label class="mcp-item" title="${escapeHtml(String(hint))}">
                  <input type="checkbox" data-forward-mcp="${escapeHtml(m.id)}" ${isChecked ? "checked" : ""} />
                  <div class="mcp-item-body">
                    <div class="mcp-item-head">
                      <code>${escapeHtml(m.id)}</code>
                      <span class="badge">${escapeHtml(m.type)}${m.enabled ? "" : " | disabled"}</span>
                    </div>
                    <div class="mcp-item-sub">${escapeHtml(String(hint || ""))}</div>
                  </div>
                </label>`;
              })
              .join("")}
          </div>`
    }
  </div>`;

  // Models Catalog Card
  const q = st.searchQuery.trim().toLowerCase();
  const visibleModels = d.models.filter((m) => {
    if (!q) return true;
    return (
      m.slug.toLowerCase().includes(q) ||
      (m.display_name || "").toLowerCase().includes(q) ||
      (m.provider || "").toLowerCase().includes(q)
    );
  });
  const excludedSet = new Set(cfg.excluded_models || []);

  const catalogCardHtml = `<div class="section-card">
    <div class="section-title">
      <div class="label-with-tip">
        <span>可用模型目录 (${visibleModels.length} / ${d.all_models_count})</span>
        ${renderHelpTip(
          "direct-mode",
          "tool_mode: direct 受限与目录过滤",
          "列表已过滤 `visibility=hide` 隐藏模型。标记为 `受限: direct` 的条目（`tool_mode: direct` 或 `supports_search_tool=false`）不会加载 `fastctx` 等透传 MCP 与原生搜索工具。",
        )}
      </div>
      <input type="search" id="model-search-input" class="compact-search" value="${escapeHtml(st.searchQuery)}" placeholder="搜索模型 slug / 名称..." />
    </div>
    <div class="catalog-list">
      ${
        visibleModels.length === 0
          ? `<div class="empty-hint">没有匹配的可见模型。</div>`
          : visibleModels
              .map((m) => {
                const isExcl = excludedSet.has(m.slug) || Boolean(m.excluded);
                const isOrchGuarded = Boolean(m.orchestrator_excluded) && st.excludeOrchestratorModels;
                const isDirectRestricted =
                  m.tool_mode === "direct" || m.supports_search_tool === false;

                let compatBadge = `<span class="badge badge-ok">MCP 兼容</span>`;
                if (isOrchGuarded) {
                  compatBadge = `<span class="badge badge-err">已排除 (Orchestrator 保护)</span>`;
                } else if (isDirectRestricted) {
                  compatBadge = `<span class="badge badge-warn">受限: direct</span>`;
                }

                const levels =
                  (m.supported_reasoning_levels || []).length > 0
                    ? m.supported_reasoning_levels.join(" / ")
                    : "任意";
                const metaParts = [
                  m.provider ? `Provider: ${m.provider}` : "",
                  `档位: ${levels}`,
                  m.context_window ? `上下文 ${formatNumber(m.context_window)}` : "",
                  `来源: ${m.source}`,
                ].filter(Boolean);

                return `<div class="catalog-row">
                  <div class="catalog-row-main">
                    <div class="catalog-row-head">
                      <code class="catalog-slug">${escapeHtml(m.slug)}</code>
                      ${compatBadge}
                      ${isExcl && !isOrchGuarded ? `<span class="badge badge-warn">已排除</span>` : ""}
                    </div>
                    <div class="catalog-row-meta">${escapeHtml(metaParts.join(" | "))}</div>
                  </div>
                  <div class="catalog-row-actions">
                    ${
                      isOrchGuarded
                        ? `<span class="badge">保护开关已锁定</span>`
                        : `<button type="button" class="btn btn-sm" data-pick-default-model="${escapeHtml(m.slug)}">设为默认</button>
                           <button type="button" class="btn btn-sm" data-toggle-exclude="${escapeHtml(m.slug)}">${isExcl ? "取消排除" : "排除"}</button>`
                    }
                  </div>
                </div>`;
              })
              .join("")
      }
    </div>
  </div>`;

  // Presets Card
  const presetsRows = Object.entries(cfg.presets || {})
    .map(
      ([pname, p]) => `<tr>
        <td><code>${escapeHtml(pname)}</code> <span class="trace-muted">${escapeHtml(p.label)}</span></td>
        <td><input type="text" data-preset-field="model" data-preset-name="${escapeHtml(pname)}" value="${escapeHtml(p.model || "")}" placeholder="继承默认" /></td>
        <td><input type="text" data-preset-field="provider" data-preset-name="${escapeHtml(pname)}" value="${escapeHtml(p.provider || "")}" placeholder="继承默认" /></td>
        <td>
          <select data-preset-field="effort" data-preset-name="${escapeHtml(pname)}">
            <option value="">默认</option>
            ${d.standard_efforts
              .map(
                (lv) =>
                  `<option value="${escapeHtml(lv)}"${p.effort === lv ? " selected" : ""}>${escapeHtml(lv)}</option>`,
              )
              .join("")}
          </select>
        </td>
        <td>
          <select data-preset-field="mode" data-preset-name="${escapeHtml(pname)}">
            <option value="write"${p.mode === "write" ? " selected" : ""}>write</option>
            <option value="read-only"${p.mode === "read-only" ? " selected" : ""}>read-only</option>
          </select>
        </td>
      </tr>`,
    )
    .join("");

  const presetsCardHtml = `<div class="section-card">
    <div class="section-title">
      <span>角色预设路由 (Presets)</span>
      <button type="button" class="btn btn-sm" id="add-preset-btn">+ 新增预设</button>
    </div>
    <div class="table-scroll">
      <table class="data-table">
        <thead><tr><th>Preset</th><th>模型</th><th>Provider</th><th>Effort</th><th>沙箱</th></tr></thead>
        <tbody>${presetsRows}</tbody>
      </table>
    </div>
  </div>`;

  // Custom Models + Doctor & Preview
  const customModelsHtml = `<div class="section-card">
    <div class="section-title">
      <span>自定义模型映射 (${(cfg.custom_models || []).length})</span>
      <span class="trace-muted">适用于自定义网关模型</span>
    </div>
    <div class="custom-model-form">
      <input type="text" id="new-cm-slug" placeholder="模型 slug (如 my-gateway/gpt-5)" />
      <input type="text" id="new-cm-provider" placeholder="Provider (可选)" />
      <input type="text" id="new-cm-efforts" placeholder="支持档位 (如 low,medium,high)" />
      <button type="button" class="btn btn-sm" id="add-custom-model-btn">+ 添加</button>
    </div>
    ${
      (cfg.custom_models || []).length > 0
        ? `<div class="chip-row" style="margin-top:8px">
            ${cfg.custom_models
              .map(
                (cm) => `<span class="badge">${escapeHtml(cm.slug)}${cm.provider ? ` (${escapeHtml(cm.provider)})` : ""}
                  <button type="button" class="chip-del-btn" data-del-custom="${escapeHtml(cm.slug)}" aria-label="删除 ${escapeHtml(cm.slug)}">×</button>
                </span>`,
              )
              .join("")}
          </div>`
        : ""
    }
  </div>`;

  const docBadges = doc
    ? Object.entries(doc.checks || {})
        .map(([k, v]) => {
          const cls = v.ok ? "badge-ok" : v.required ? "badge-err" : "badge-warn";
          return `<span class="badge ${cls}" title="${escapeHtml(v.error || v.hint || v.version || "")}">${escapeHtml(k)}: ${v.ok ? "OK" : v.required ? "FAIL" : "WARN"}</span>`;
        })
        .join("")
    : "";

  const doctorPreviewCardHtml = `<div class="section-card">
    <div class="section-title">
      <span>环境自检与命令行预览</span>
      <button type="button" class="btn btn-sm" id="run-doctor-btn">重新体检</button>
    </div>
    <div class="chip-row" style="margin-bottom:6px">${docBadges || `<span class="trace-muted">点击重新体检查看环境检查结果</span>`}</div>
    <div class="trace-muted">
      生效默认: <code>${escapeHtml(String(effDef.model || "未指定"))}</code> |
      Provider: <code>${escapeHtml(String(effDef.provider || "默认"))}</code> |
      Effort: <code>${escapeHtml(String(effDef.effort || "默认"))}</code> |
      目录模式: <code>${escapeHtml(d.catalog_mode)}</code>
    </div>
    ${
      st.preview
        ? `<div style="margin-top:8px">
            <div class="trace-muted">命令预览 (已自动脱敏):</div>
            <pre class="code-box">${escapeHtml(st.preview.command_line)}</pre>
            ${
              st.preview.warnings && st.preview.warnings.length > 0
                ? `<div class="banner-warn" style="margin-top:6px">${st.preview.warnings.map((w) => escapeHtml(w)).join("<br/>")}</div>`
                : ""
            }
          </div>`
        : ""
    }
  </div>`;

  return `<div class="scroll-y custom-scroll config-view-root">
    <div class="page-stack">
      ${feedbackHtml}
      ${switchCardHtml}
      <div class="config-grid">
        ${defaultsCardHtml}
        ${mcpCardHtml}
      </div>
      ${catalogCardHtml}
      ${presetsCardHtml}
      ${customModelsHtml}
      ${doctorPreviewCardHtml}
    </div>
  </div>`;
}
