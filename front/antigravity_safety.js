// Antigravity safetySettings 控制面板扩展。
// 在 common.js 之后加载，只维护自身状态，并在桌面端/移动端的 Antigravity 兼容设置旁插入 UI。
(function () {
    'use strict';

    const state = {
        models: [],
        categoriesByModel: {},
        modelLabels: {},
        defaultCategories: [],
        rules: []
    };

    function esc(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function injectStyles() {
        if (document.getElementById('antigravitySafetyStyles')) return;
        const style = document.createElement('style');
        style.id = 'antigravitySafetyStyles';
        style.textContent = `
            .ag-safety-box { margin-top: 14px; padding: 14px; border: 1px solid #90caf9; border-radius: 8px; background: #f7fbff; }
            .ag-safety-title { font-weight: 700; color: #0d47a1; margin-bottom: 10px; }
            .ag-safety-grid { display: grid; grid-template-columns: minmax(180px, 1.1fr) minmax(180px, .9fr) minmax(280px, 2fr) auto; gap: 10px; align-items: start; }
            .ag-safety-head { font-size: 12px; font-weight: 700; color: #455a64; padding: 0 4px; }
            .ag-safety-row { display: contents; }
            .ag-safety-row select { width: 100%; min-height: 36px; }
            .ag-safety-categories { display: flex; flex-wrap: wrap; gap: 6px; min-height: 36px; align-items: center; }
            .ag-safety-chip { border: 1px solid #9e9e9e; border-radius: 999px; padding: 5px 8px; background: #fff; color: #37474f; cursor: pointer; font-size: 11px; line-height: 1.2; }
            .ag-safety-chip.excluded { border-color: #c62828; background: #ffebee; color: #b71c1c; text-decoration: line-through; font-weight: 700; }
            .ag-safety-chip.unsupported { cursor: not-allowed; opacity: .78; }
            .ag-safety-delete { border: 1px solid #ef9a9a; background: #fff; color: #c62828; border-radius: 5px; padding: 7px 10px; cursor: pointer; }
            .ag-safety-unavailable { color: #ef6c00; font-size: 11px; margin-top: 4px; }
            .ag-safety-empty { grid-column: 1 / -1; color: #78909c; padding: 10px 4px; font-size: 13px; }
            .ag-safety-disabled { opacity: .55; }
            .ag-safety-toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 10px; }
            .ag-safety-toolbar button { padding: 7px 11px; cursor: pointer; }
            .ag-safety-search { display: none; gap: 10px; align-items: center; margin: 8px 0 10px; }
            .ag-safety-search.visible { display: flex; }
            .ag-safety-search input { flex: 1; min-width: 180px; padding: 7px 9px; border: 1px solid #b0bec5; border-radius: 5px; background: #fff; }
            .ag-safety-search-count { flex: 0 0 auto; color: #607d8b; font-size: 12px; white-space: nowrap; }
            .ag-safety-scroll.scrollable { max-height: 420px; overflow-y: auto; overscroll-behavior: contain; padding-right: 6px; }
            .ag-safety-scroll.scrollable .ag-safety-head { position: sticky; top: 0; z-index: 2; background: #f7fbff; padding-top: 5px; padding-bottom: 5px; }
            .ag-safety-search-empty { display: none; color: #78909c; font-size: 12px; padding: 8px 4px; }
            .ag-safety-help { margin-top: 8px; color: #546e7a; font-size: 12px; line-height: 1.5; }
            @media (max-width: 780px) {
                .ag-safety-scroll.scrollable { max-height: 55vh; }
                .ag-safety-search.visible { align-items: stretch; }
                .ag-safety-grid { display: block; }
                .ag-safety-head { display: none; }
                .ag-safety-row { display: block; padding: 10px 0; border-top: 1px solid #dbe9f3; }
                .ag-safety-row > * { margin-bottom: 8px; }
                .ag-safety-row select { width: 100%; }
                .ag-safety-categories { margin-top: 4px; }
            }
        `;
        document.head.appendChild(style);
    }

    function injectUI() {
        if (document.getElementById('antigravitySafetySettingsEnabled')) return;
        const anchor = document.getElementById('antigravitySwitchCredentialEnabled');
        if (!anchor) return;
        const anchorGroup = anchor.closest('.form-group');
        if (!anchorGroup) return;

        injectStyles();
        const box = document.createElement('div');
        box.className = 'ag-safety-box';
        box.id = 'antigravitySafetyBox';
        box.innerHTML = `
            <div class="ag-safety-title">🛡️ Antigravity safetySettings 兼容性控制</div>
            <div class="form-group">
                <label>
                    <input type="checkbox" id="antigravitySafetySettingsEnabled" class="config-checkbox" />
                    向 Antigravity 发送 safetySettings
                </label>
                <small class="config-note">关闭时保持原有行为：从最终请求中移除 safetySettings。开启时保留 gcli2api 当前按模型生成的完整 category 列表，并使用下方 threshold。支持热更新。</small>
            </div>
            <div class="form-group">
                <label for="antigravitySafetyThreshold">Safety threshold:</label>
                <select id="antigravitySafetyThreshold" class="config-input">
                    <option value="BLOCK_NONE">BLOCK_NONE</option>
                    <option value="OFF">OFF</option>
                </select>
                <small class="config-note">仅控制请求中发送的可配置 threshold；不代表关闭模型或产品层的其他安全机制。</small>
            </div>
            <div class="form-group">
                <label>
                    <input type="checkbox" id="antigravitySafetyModelRulesEnabled" class="config-checkbox" />
                    启用 Model Safety Overrides
                </label>
                <small class="config-note">规则按最终发送给 Antigravity 的 canonical model ID 精确匹配。因此抗截断/假流式等本地变体会自动继承同一后端模型的规则。</small>
            </div>
            <div id="antigravitySafetyRulesArea">
                <div class="ag-safety-search" id="antigravitySafetyRuleSearchBar">
                    <input type="search" id="antigravitySafetyRuleSearch" placeholder="搜索已添加模型…" autocomplete="off" />
                    <span class="ag-safety-search-count" id="antigravitySafetyRuleCount"></span>
                </div>
                <div class="ag-safety-scroll" id="antigravitySafetyRulesScroll">
                    <div class="ag-safety-grid" id="antigravitySafetyRulesGrid"></div>
                </div>
                <div class="ag-safety-search-empty" id="antigravitySafetySearchEmpty">没有匹配的已添加模型</div>
                <div class="ag-safety-toolbar">
                    <button type="button" id="addAntigravitySafetyRuleBtn">＋ 添加模型规则</button>
                    <span style="font-size:12px;color:#607d8b;">红色 category = 不发送；模型本身不支持的 category 会默认标红并锁定，手动排除的 category 可再次点击恢复。</span>
                </div>
            </div>
            <div class="ag-safety-help">模型列表从 Antigravity <code>fetchAvailableModels</code> 获取。界面可使用更易懂的显示名，但规则内部仍绑定 Google 返回的 canonical model ID。若已保存的模型暂时不在当前列表中，该规则仍保留并可删除。</div>
        `;
        anchorGroup.insertAdjacentElement('afterend', box);

        document.getElementById('antigravitySafetySettingsEnabled').addEventListener('change', refreshEnabledState);
        document.getElementById('antigravitySafetyModelRulesEnabled').addEventListener('change', refreshEnabledState);
        document.getElementById('addAntigravitySafetyRuleBtn').addEventListener('click', addRule);
        document.getElementById('antigravitySafetyRuleSearch').addEventListener('input', applyRuleSearch);
    }

    function isEnvLocked(key) {
        return !!(typeof AppState !== 'undefined' && AppState.envLockedFields && AppState.envLockedFields.has(key));
    }

    function getCategories(model) {
        const key = String(model || '').toLowerCase();
        const categories = state.categoriesByModel[key];
        return Array.isArray(categories) && categories.length
            ? [...categories]
            : [...state.defaultCategories];
    }

    function selectedModelsExcept(index) {
        return new Set(
            state.rules
                .map((rule, i) => i === index ? '' : String(rule.model || '').toLowerCase())
                .filter(Boolean)
        );
    }

    function applyRuleSearch() {
        const input = document.getElementById('antigravitySafetyRuleSearch');
        const count = document.getElementById('antigravitySafetyRuleCount');
        const empty = document.getElementById('antigravitySafetySearchEmpty');
        const query = String(input?.value || '').trim().toLowerCase();
        const rows = Array.from(document.querySelectorAll('#antigravitySafetyRulesGrid [data-rule-row]'));
        let matched = 0;

        rows.forEach(row => {
            const searchable = String(row.dataset.search || '').toLowerCase();
            const visible = !query || searchable.includes(query);
            if (visible) {
                row.style.removeProperty('display');
                matched += 1;
            } else {
                row.style.display = 'none';
            }
        });

        if (count) count.textContent = query ? `匹配: ${matched} / ${state.rules.length}` : `当前规则: ${state.rules.length}`;
        if (empty) empty.style.display = state.rules.length && query && matched === 0 ? 'block' : 'none';
    }

    function renderRules() {
        const grid = document.getElementById('antigravitySafetyRulesGrid');
        const searchBar = document.getElementById('antigravitySafetyRuleSearchBar');
        const searchInput = document.getElementById('antigravitySafetyRuleSearch');
        const scroll = document.getElementById('antigravitySafetyRulesScroll');
        const searchEmpty = document.getElementById('antigravitySafetySearchEmpty');
        if (!grid) return;

        const useCompactList = state.rules.length >= 2;
        if (searchBar) searchBar.classList.toggle('visible', useCompactList);
        if (scroll) scroll.classList.toggle('scrollable', useCompactList);
        if (!useCompactList && searchInput) searchInput.value = '';
        if (searchEmpty) searchEmpty.style.display = 'none';

        if (!state.rules.length) {
            grid.innerHTML = '<div class="ag-safety-empty">暂无模型规则。未匹配模型将使用全局 safetySettings 配置。</div>';
            applyRuleSearch();
            refreshEnabledState();
            return;
        }

        let html = `
            <div class="ag-safety-head">模型</div>
            <div class="ag-safety-head">模式</div>
            <div class="ag-safety-head">safetySettings categories</div>
            <div class="ag-safety-head"></div>
        `;

        state.rules.forEach((rule, index) => {
            const model = String(rule.model || '').toLowerCase();
            const displayLabel = state.modelLabels[model] || model;
            const searchable = `${model} ${displayLabel}`.toLowerCase();
            const usedElsewhere = selectedModelsExcept(index);
            const currentAvailable = state.models.includes(model);
            const optionModels = currentAvailable || !model ? [...state.models] : [model, ...state.models];
            const uniqueModels = [...new Set(optionModels)];
            const modelOptions = uniqueModels.map(option => {
                const unavailable = !state.models.includes(option);
                const disabled = usedElsewhere.has(option);
                const optionLabel = state.modelLabels[option] || option;
                return `<option value="${esc(option)}" ${option === model ? 'selected' : ''} ${disabled ? 'disabled' : ''}>${esc(optionLabel)}${unavailable ? ' (当前列表不可用)' : ''}</option>`;
            }).join('');

            const mode = rule.mode === 'exclude_all' ? 'exclude_all' : 'filter_categories';
            const excluded = new Set(Array.isArray(rule.excluded_categories) ? rule.excluded_categories : []);
            const supportedCategories = new Set(getCategories(model));
            const categories = state.defaultCategories.length ? [...state.defaultCategories] : [...supportedCategories];
            const chips = mode === 'filter_categories'
                ? categories.map(category => {
                    const unsupported = !supportedCategories.has(category);
                    const visuallyExcluded = unsupported || excluded.has(category);
                    const title = unsupported
                        ? '该模型的 safetySettings 不包含此 category，因此本来就不会发送'
                        : (excluded.has(category) ? '已排除，不会发送' : '当前会发送');
                    return `
                    <button type="button" class="ag-safety-chip ${visuallyExcluded ? 'excluded' : ''} ${unsupported ? 'unsupported' : ''}"
                        data-rule-index="${index}" data-category="${esc(category)}" data-unsupported="${unsupported ? 'true' : 'false'}"
                        ${unsupported ? 'disabled' : ''} title="${esc(title)}">${esc(category)}</button>
                    `;
                }).join('')
                : '<span style="font-size:12px;color:#b71c1c;font-weight:700;">整个 safetySettings 字段将被移除</span>';

            html += `
                <div class="ag-safety-row" data-rule-row="${index}" data-search="${esc(searchable)}">
                    <div>
                        <select class="config-input ag-safety-model" data-rule-index="${index}">${modelOptions}</select>
                        ${!currentAvailable && model ? '<div class="ag-safety-unavailable">⚠ 当前 fetchAvailableModels 未返回该模型；规则仍会保留。</div>' : ''}
                    </div>
                    <div>
                        <select class="config-input ag-safety-mode" data-rule-index="${index}">
                            <option value="exclude_all" ${mode === 'exclude_all' ? 'selected' : ''}>排除发送 safetySettings</option>
                            <option value="filter_categories" ${mode === 'filter_categories' ? 'selected' : ''}>过滤 category</option>
                        </select>
                    </div>
                    <div class="ag-safety-categories">${chips}</div>
                    <div><button type="button" class="ag-safety-delete" data-rule-index="${index}">删除</button></div>
                </div>
            `;
        });

        grid.innerHTML = html;

        grid.querySelectorAll('.ag-safety-model').forEach(select => {
            select.addEventListener('change', event => changeRuleModel(Number(event.target.dataset.ruleIndex), event.target.value));
        });
        grid.querySelectorAll('.ag-safety-mode').forEach(select => {
            select.addEventListener('change', event => changeRuleMode(Number(event.target.dataset.ruleIndex), event.target.value));
        });
        grid.querySelectorAll('.ag-safety-chip').forEach(chip => {
            chip.addEventListener('click', event => toggleCategory(Number(event.currentTarget.dataset.ruleIndex), event.currentTarget.dataset.category));
        });
        grid.querySelectorAll('.ag-safety-delete').forEach(button => {
            button.addEventListener('click', event => deleteRule(Number(event.currentTarget.dataset.ruleIndex)));
        });

        applyRuleSearch();
        refreshEnabledState();
    }

    function refreshEnabledState() {
        const enabled = !!document.getElementById('antigravitySafetySettingsEnabled')?.checked;
        const rulesEnabled = !!document.getElementById('antigravitySafetyModelRulesEnabled')?.checked;
        const threshold = document.getElementById('antigravitySafetyThreshold');
        const rulesToggle = document.getElementById('antigravitySafetyModelRulesEnabled');
        const rulesArea = document.getElementById('antigravitySafetyRulesArea');
        const addButton = document.getElementById('addAntigravitySafetyRuleBtn');

        if (threshold) threshold.disabled = !enabled || isEnvLocked('antigravity_safety_threshold');
        if (rulesToggle) rulesToggle.disabled = !enabled || isEnvLocked('antigravity_safety_model_rules_enabled');
        if (rulesArea) rulesArea.classList.toggle('ag-safety-disabled', !enabled || !rulesEnabled);
        if (addButton) addButton.disabled = !enabled || !rulesEnabled;

        document.querySelectorAll('#antigravitySafetyRulesGrid select, #antigravitySafetyRulesGrid button').forEach(el => {
            const modelUnsupported = el.dataset?.unsupported === 'true';
            el.disabled = !enabled || !rulesEnabled || modelUnsupported;
        });

        const globalToggle = document.getElementById('antigravitySafetySettingsEnabled');
        if (globalToggle && isEnvLocked('antigravity_safety_settings_enabled')) globalToggle.disabled = true;
    }

    function addRule() {
        const used = new Set(state.rules.map(rule => String(rule.model || '').toLowerCase()).filter(Boolean));
        const model = state.models.find(candidate => !used.has(candidate));
        if (!model) {
            if (typeof showStatus === 'function') {
                showStatus(state.models.length ? '所有当前可用模型都已有规则' : '当前没有可用的Antigravity模型，请先确认凭证可用', 'error');
            }
            return;
        }
        state.rules.push({ model, mode: 'filter_categories', excluded_categories: [] });
        renderRules();
    }

    function deleteRule(index) {
        if (!Number.isInteger(index) || index < 0 || index >= state.rules.length) return;
        state.rules.splice(index, 1);
        renderRules();
    }

    function changeRuleModel(index, model) {
        const rule = state.rules[index];
        if (!rule) return;
        rule.model = String(model || '').toLowerCase();
        const allowed = new Set(getCategories(rule.model));
        rule.excluded_categories = (rule.excluded_categories || []).filter(category => allowed.has(category));
        renderRules();
    }

    function changeRuleMode(index, mode) {
        const rule = state.rules[index];
        if (!rule) return;
        rule.mode = mode === 'exclude_all' ? 'exclude_all' : 'filter_categories';
        if (rule.mode === 'exclude_all') rule.excluded_categories = [];
        renderRules();
    }

    function toggleCategory(index, category) {
        const rule = state.rules[index];
        if (!rule || rule.mode !== 'filter_categories') return;
        if (!getCategories(rule.model).includes(category)) return;
        const excluded = new Set(rule.excluded_categories || []);
        if (excluded.has(category)) excluded.delete(category);
        else excluded.add(category);
        rule.excluded_categories = getCategories(rule.model).filter(item => excluded.has(item));
        renderRules();
    }

    function collectConfig() {
        return {
            antigravity_safety_settings_enabled: !!document.getElementById('antigravitySafetySettingsEnabled')?.checked,
            antigravity_safety_threshold: document.getElementById('antigravitySafetyThreshold')?.value || 'BLOCK_NONE',
            antigravity_safety_model_rules_enabled: !!document.getElementById('antigravitySafetyModelRulesEnabled')?.checked,
            // 规则数组按整体替换保存；删除一行后，该模型对象不会再发送给后端。
            antigravity_safety_model_rules: state.rules.map(rule => ({
                model: String(rule.model || '').toLowerCase(),
                mode: rule.mode === 'exclude_all' ? 'exclude_all' : 'filter_categories',
                excluded_categories: Array.isArray(rule.excluded_categories) ? [...rule.excluded_categories] : []
            }))
        };
    }

    async function loadOptionsAndPopulate() {
        injectUI();
        const c = (typeof AppState !== 'undefined' && AppState.currentConfig) || {};
        state.rules = Array.isArray(c.antigravity_safety_model_rules)
            ? c.antigravity_safety_model_rules.map(rule => ({
                model: String(rule?.model || '').toLowerCase(),
                mode: rule?.mode === 'exclude_all' ? 'exclude_all' : 'filter_categories',
                excluded_categories: Array.isArray(rule?.excluded_categories) ? [...rule.excluded_categories] : []
            })).filter(rule => rule.model)
            : [];

        const enabled = document.getElementById('antigravitySafetySettingsEnabled');
        const threshold = document.getElementById('antigravitySafetyThreshold');
        const rulesEnabled = document.getElementById('antigravitySafetyModelRulesEnabled');
        if (enabled) enabled.checked = Boolean(c.antigravity_safety_settings_enabled);
        if (threshold) threshold.value = c.antigravity_safety_threshold === 'OFF' ? 'OFF' : 'BLOCK_NONE';
        if (rulesEnabled) rulesEnabled.checked = c.antigravity_safety_model_rules_enabled !== false;

        try {
            const response = await fetch('./config/antigravity-safety-options', { headers: getAuthHeaders() });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || data.error || '加载模型选项失败');
            state.models = Array.isArray(data.models) ? data.models.map(model => String(model).toLowerCase()) : [];
            state.categoriesByModel = data.categories_by_model && typeof data.categories_by_model === 'object'
                ? Object.fromEntries(Object.entries(data.categories_by_model).map(([model, categories]) => [String(model).toLowerCase(), Array.isArray(categories) ? categories : []]))
                : {};
            state.modelLabels = data.model_labels && typeof data.model_labels === 'object'
                ? Object.fromEntries(Object.entries(data.model_labels).map(([model, label]) => [String(model).toLowerCase(), String(label || model)]))
                : {};
            state.defaultCategories = Array.isArray(data.default_categories) ? data.default_categories : [];
        } catch (error) {
            state.models = [];
            state.categoriesByModel = {};
            state.modelLabels = {};
            state.defaultCategories = [];
            console.warn('Antigravity safety options load failed:', error);
            if (typeof showStatus === 'function') showStatus(`Safety模型选项加载失败: ${error.message}`, 'error');
        }

        renderRules();
        refreshEnabledState();
    }

    function wrapLoadConfig() {
        if (typeof window.loadConfig === 'function' && !window.loadConfig.__agSafetyWrapped) {
            const originalLoadConfig = window.loadConfig;
            const wrappedLoadConfig = async function (...args) {
                const result = await originalLoadConfig.apply(this, args);
                await loadOptionsAndPopulate();
                return result;
            };
            wrappedLoadConfig.__agSafetyWrapped = true;
            window.loadConfig = wrappedLoadConfig;
        }
    }

    injectUI();
    wrapLoadConfig();
    window.AntigravitySafetyUI = { state, collectConfig, loadOptionsAndPopulate, renderRules };
})();
