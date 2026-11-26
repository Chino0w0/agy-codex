import type { CommandPreviewPayload, WorkbenchConfig, WorkbenchPayload } from "../types";
import { escapeHtml, formatNumber } from "../utils";

export interface ModelsViewState {
  data: WorkbenchPayload | null;
  draft: WorkbenchConfig | null;
  preview: CommandPreviewPayload | null;
  searchQuery: string;
  includeHidden: boolean;
  saving: boolean;
  statusMsg: string | null;
  errorMsg: string | null;
}

export function renderModelsTab(st: ModelsViewState): string {
  if (!st.data || !st.draft) {
    return `<div class="empty-hint">正在加载模型目录与配置中心...</div>`;
  }

  const d = st.data;
  const cfg = st.draft;
  const doc = d.doctor;
  const effDef = d.effective_defaults || {};

  const docBadges = doc
    ? Object.entries(doc.checks || {})
        .map(([k, v]) => {
          const cls = v.ok ? "badge-ok" : v.required ? "badge-err" : "badge-warn";
          return `<span class="badge ${cls}" title="${escapeHtml(v.error || v.hint || v.version || "")}">${escapeHtml(k)}: ${v.ok ? "OK" : v.required ? "FAIL" : "WARN"}</span>`;
        })
        .join("")
    : "";

  const doctorHtml = `<div class="section-card">
    <div class="section-title">
      <span>环境自检与当前生效配置</span>
      <button class="btn btn-sm" id="run-doctor-btn">重新体检</button>
    </div>
    <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px">${docBadges}</div>
    <div class="trace-meta">
      当前生效默认: <code>${escapeHtml(String(effDef.model || "未指定"))}</code> ·
      Provider: <code>${escapeHtml(String(effDef.provider || "默认"))}</code> ·
      Effort: <code>${escapeHtml(String(effDef.effort || "默认"))}</code> ·
      目录模式: <code>${escapeHtml(d.catalog_mode)}</code>
      ${d.catalog_path ? `(${escapeHtml(d.catalog_path)})` : ""}
    </div>
  </div>`;

  const feedbackHtml = st.statusMsg
    ? `<div class="banner-ok" style="margin-bottom:8px">${escapeHtml(st.statusMsg)}</div>`
    : st.errorMsg
      ? `<div class="banner-warn" style="margin-bottom:8px">${escapeHtml(st.errorMsg)}</div>`
      : "";

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

  const settingsHtml = `<div class="section-card">
    <div class="section-title">
      <span>默认 Worker 参数与灵活映射策略</span>
      <div style="display:flex;gap:6px">
        <button class="btn btn-sm" id="preview-cmd-btn">预览命令行参数</button>
        <button class="btn btn-primary btn-sm" id="save-workbench-btn">${st.saving ? "保存中..." : "保存配置"}</button>
      </div>
    </div>
    <div class="form-grid" style="margin-bottom:8px">
      <div class="field">
        <label>默认 Provider</label>
        <select id="cfg-def-provider">${providerOptions}</select>
      </div>
      <div class="field">
        <label>默认模型 (可手输任意模型名，留空继承)</label>
        <input type="text" id="cfg-def-model" list="known-models-datalist" value="${escapeHtml(cfg.defaults.model || "")}" placeholder="留空继承 config.toml"/>
        <datalist id="known-models-datalist">
          ${d.models.map((m) => `<option value="${escapeHtml(m.slug)}">${escapeHtml(m.display_name)}</option>`).join("")}
        </datalist>
      </div>
      <div class="field">
        <label>默认推理档位${matchedModelObj?.supported_reasoning_levels?.length ? ` (该模型支持: ${escapeHtml(matchedModelObj.supported_reasoning_levels.join("/"))})` : ""}</label>
        <select id="cfg-def-effort">
          <option value="">(继承 config.toml)</option>
          ${effortChoices.map((lv) => `<option value="${escapeHtml(lv)}"${cfg.defaults.effort === lv ? " selected" : ""}>${escapeHtml(lv)}</option>`).join("")}
        </select>
      </div>
      <div class="field">
        <label>配置隔离模式 (config_mode)</label>
        <select id="cfg-def-config-mode">
          <option value="isolated"${cfg.defaults.config_mode === "isolated" ? " selected" : ""}>isolated (白名单隔离, 推荐)</option>
          <option value="profile"${cfg.defaults.config_mode === "profile" ? " selected" : ""}>profile (-p worker)</option>
          <option value="inherit"${cfg.defaults.config_mode === "inherit" ? " selected" : ""}>inherit (继承完整 config.toml)</option>
        </select>
      </div>
    </div>

    <div class="form-grid" style="margin-bottom:8px">
      <div class="field">
        <label>Catalog 映射模式 (catalog_mode)</label>
        <select id="cfg-map-catalog-mode">
          <option value="auto"${cfg.mapping.catalog_mode === "auto" ? " selected" : ""}>auto (自动透传配置中的 model_catalog_json)</option>
          <option value="custom"${cfg.mapping.catalog_mode === "custom" ? " selected" : ""}>custom (使用下方自定义 Catalog 路径)</option>
          <option value="off"${cfg.mapping.catalog_mode === "off" ? " selected" : ""}>off (禁用 model_catalog_json 注入)</option>
        </select>
      </div>
      <div class="field">
        <label>自定义 Catalog 路径 (catalog_path_override)</label>
        <input type="text" id="cfg-map-catalog-path" value="${escapeHtml(cfg.mapping.catalog_path_override || "")}" placeholder="相对 CODEX_HOME 或绝对路径"/>
      </div>
      <div class="field">
        <label>推理档位策略 (effort_policy)</label>
        <select id="cfg-map-effort-policy">
          <option value="clamp_with_warning"${cfg.mapping.effort_policy === "clamp_with_warning" ? " selected" : ""}>clamp_with_warning (不支持时平滑降级并提示)</option>
          <option value="passthrough"${cfg.mapping.effort_policy === "passthrough" ? " selected" : ""}>passthrough (原样透传不收敛)</option>
        </select>
      </div>
      <div class="field">
        <label>上下文窗口透传策略 (context_window_policy)</label>
        <select id="cfg-map-cw-policy">
          <option value="same_model_only"${cfg.mapping.context_window_policy === "same_model_only" ? " selected" : ""}>same_model_only (仅同模型时透传)</option>
          <option value="always_forward"${cfg.mapping.context_window_policy === "always_forward" ? " selected" : ""}>always_forward (总是透传)</option>
          <option value="never_forward"${cfg.mapping.context_window_policy === "never_forward" ? " selected" : ""}>never_forward (不透传)</option>
        </select>
      </div>
      <div class="field">
        <label>额外白名单键 (逗号分隔)</label>
        <input type="text" id="cfg-map-extra-keys" value="${escapeHtml((cfg.mapping.extra_whitelist_keys || []).join(", "))}" placeholder="如 web_search, allow_login_shell"/>
      </div>
      <div class="field">
        <label>并行槽位上限 (max_concurrent_runs)</label>
        <input type="number" id="cfg-map-max-conc" min="1" max="20" value="${cfg.mapping.max_concurrent_runs}"/>
      </div>
      <div class="field" style="justify-content:flex-end">
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer;padding-top:16px">
          <input type="checkbox" id="cfg-map-allow-nested" ${cfg.mapping.allow_nested_subagents ? "checked" : ""}/>
          允许 Codex 内部派生子线程 (allow_nested_subagents)
        </label>
      </div>
    </div>

    ${
      (d.mcp_servers || []).length > 0
        ? `<div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--color-border-primary,rgba(128,128,128,0.16))">
            <div style="font-size:11.5px;color:var(--color-text-secondary,#8b949e);margin-bottom:6px">
              隔离模式透传的 Codex MCP 工具 (<code>forwarded_mcp_servers</code>，默认全关以防桌面 MCP 阻塞)
            </div>
            <div style="display:flex;flex-wrap:wrap;gap:8px">
              ${(d.mcp_servers || [])
                .map((m) => {
                  const fwdSet = new Set(cfg.mapping.forwarded_mcp_servers || []);
                  const isChecked = fwdSet.has(m.id);
                  const hint = m.command || m.url || m.type;
                  return `<label class="badge" style="display:inline-flex;align-items:center;gap:5px;cursor:pointer;padding:4px 8px" title="${escapeHtml(String(hint))}">
                    <input type="checkbox" data-forward-mcp="${escapeHtml(m.id)}" ${isChecked ? "checked" : ""}/>
                    <code>${escapeHtml(m.id)}</code>
                    <span class="trace-meta">${escapeHtml(m.type)}${m.enabled ? "" : ", disabled"}</span>
                  </label>`;
                })
                .join("")}
            </div>
          </div>`
        : ""
    }

    ${
      st.preview
        ? `<div style="margin-top:8px">
            <div class="trace-meta">命令预览 (已自动脱敏):</div>
            <pre class="code-box">${escapeHtml(st.preview.command_line)}</pre>
            ${
              st.preview.warnings && st.preview.warnings.length > 0
                ? `<div class="banner-warn" style="margin-top:4px">${st.preview.warnings.map((w) => escapeHtml(w)).join("<br/>")}</div>`
                : ""
            }
          </div>`
        : ""
    }
  </div>`;

  const presetsRows = Object.entries(cfg.presets || {})
    .map(
      ([pname, p]) => `<tr>
        <td><code>${escapeHtml(pname)}</code> <span class="trace-meta">${escapeHtml(p.label)}</span></td>
        <td><input type="text" data-preset-field="model" data-preset-name="${escapeHtml(pname)}" value="${escapeHtml(p.model || "")}" placeholder="继承默认"/></td>
        <td><input type="text" data-preset-field="provider" data-preset-name="${escapeHtml(pname)}" value="${escapeHtml(p.provider || "")}" placeholder="继承默认"/></td>
        <td>
          <select data-preset-field="effort" data-preset-name="${escapeHtml(pname)}">
            <option value="">默认</option>
            ${d.standard_efforts.map((lv) => `<option value="${escapeHtml(lv)}"${p.effort === lv ? " selected" : ""}>${escapeHtml(lv)}</option>`).join("")}
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

  const presetsHtml = `<div class="section-card">
    <div class="section-title">
      <span>角色预设路由 (Presets)</span>
      <button class="btn btn-sm" id="add-preset-btn">+ 新增预设</button>
    </div>
    <table class="data-table">
      <thead><tr><th>Preset</th><th>模型</th><th>Provider</th><th>Effort</th><th>沙箱</th></tr></thead>
      <tbody>${presetsRows}</tbody>
    </table>
  </div>`;

  const customModelsHtml = `<div class="section-card">
    <div class="section-title">
      <span>用户自定义模型列表 (${(cfg.custom_models || []).length})</span>
      <span class="trace-meta">适合未使用 model_catalog_json 的用户自定义映射</span>
    </div>
    <div class="form-grid" style="margin-bottom:8px">
      <input type="text" id="new-cm-slug" placeholder="模型 slug (如 my-gateway/gpt-5)"/>
      <input type="text" id="new-cm-provider" placeholder="绑定 Provider (可选)"/>
      <input type="text" id="new-cm-efforts" placeholder="支持档位逗号分隔 (如 low,medium,high,xhigh)"/>
      <button class="btn btn-sm" id="add-custom-model-btn">+ 添加自定义模型</button>
    </div>
    ${
      (cfg.custom_models || []).length > 0
        ? `<div style="display:flex;flex-wrap:wrap;gap:6px">
            ${cfg.custom_models
              .map(
                (cm) => `<span class="badge">${escapeHtml(cm.slug)} ${cm.provider ? `(${escapeHtml(cm.provider)})` : ""}
                  <button class="btn btn-sm" style="padding:0 4px;border:none" data-del-custom="${escapeHtml(cm.slug)}">×</button>
                </span>`,
              )
              .join("")}
          </div>`
        : ""
    }
  </div>`;

  const q = st.searchQuery.trim().toLowerCase();
  const visibleModels = d.models.filter((m) => {
    if (!q) return true;
    return m.slug.toLowerCase().includes(q) || m.display_name.toLowerCase().includes(q);
  });
  const excludedSet = new Set(cfg.excluded_models || []);

  const catalogHtml = `<div class="section-card">
    <div class="section-title">
      <span>可用模型目录 (${visibleModels.length} / ${d.all_models_count})</span>
      <div style="display:flex;gap:8px;align-items:center">
        <label style="font-size:11.5px;display:inline-flex;align-items:center;gap:4px;cursor:pointer">
          <input type="checkbox" id="toggle-hidden-models" ${st.includeHidden ? "checked" : ""}/>
          显示隐藏模型 (${d.hidden_models_count})
        </label>
        <input type="text" id="model-search-input" style="width:170px" value="${escapeHtml(st.searchQuery)}" placeholder="搜索模型 slug/名称..."/>
      </div>
    </div>
    <table class="data-table">
      <thead><tr><th>模型</th><th>来源</th><th>上下文</th><th>支持档位</th><th>操作</th></tr></thead>
      <tbody>
        ${visibleModels
          .map((m) => {
            const isExcl = excludedSet.has(m.slug);
            return `<tr>
              <td>
                <div><code>${escapeHtml(m.slug)}</code>${isExcl ? ` <span class="badge badge-warn">已排除</span>` : ""}</div>
                <div class="trace-meta">${escapeHtml(m.display_name)}${m.provider ? ` · ${escapeHtml(m.provider)}` : ""}</div>
              </td>
              <td><span class="badge">${escapeHtml(m.source)}</span></td>
              <td>${m.context_window ? formatNumber(m.context_window) : "-"}</td>
              <td>${(m.supported_reasoning_levels || []).map((lv) => `<span class="badge">${escapeHtml(lv)}</span>`).join(" ") || `<span class="trace-meta">任意</span>`}</td>
              <td style="white-space:nowrap">
                <button class="btn btn-sm" data-pick-default-model="${escapeHtml(m.slug)}">设为默认</button>
                <button class="btn btn-sm" data-toggle-exclude="${escapeHtml(m.slug)}">${isExcl ? "取消排除" : "排除"}</button>
              </td>
            </tr>`;
          })
          .join("")}
      </tbody>
    </table>
  </div>`;

  return `<div class="scroll-y">
    ${feedbackHtml}
    ${doctorHtml}
    ${settingsHtml}
    ${presetsHtml}
    ${customModelsHtml}
    ${catalogHtml}
  </div>`;
}
