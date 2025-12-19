/**
 * Antigravity 模型额度查看模块
 * 独立模块，用于在 Antigravity 凭证管理页面展示模型额度信息
 */

const AntigravityQuota = {
    // 缓存配置
    cache: {},
    cacheTTL: 5 * 60 * 1000,  // 5分钟缓存

    // 活跃预览实例 {containerId: filename}
    activePreviews: {},

    // 自动刷新间隔 (毫秒)
    autoRefreshIntervalMs: 60 * 1000, // 每60秒刷新一次
    autoRefreshTimerId: null,

    /**
     * 启动自动刷新
     */
    initAutoRefresh() {
        if (this.autoRefreshTimerId) return; // 已启动

        this.autoRefreshTimerId = setInterval(() => {
            console.log('[AntigravityQuota] Auto-refreshing all previews...');
            this.refreshAllPreviews();
        }, this.autoRefreshIntervalMs);

        console.log('[AntigravityQuota] Auto-refresh started.');
    },

    /**
     * 停止自动刷新
     */
    stopAutoRefresh() {
        if (this.autoRefreshTimerId) {
            clearInterval(this.autoRefreshTimerId);
            this.autoRefreshTimerId = null;
            console.log('[AntigravityQuota] Auto-refresh stopped.');
        }
    },

    /**
     * 刷新所有活跃的预览
     */
    async refreshAllPreviews() {
        for (const [containerId, filename] of Object.entries(this.activePreviews)) {
            // 强制刷新缓存
            this.clearCache(filename);
            await this.renderPreview(filename, containerId);
        }
    },

    /**
     * 获取缓存的额度数据
     */
    getCached(filename) {
        const cached = this.cache[filename];
        if (!cached) return null;
        if (Date.now() - cached.timestamp > this.cacheTTL) {
            delete this.cache[filename];
            return null;
        }
        return cached.data;
    },

    /**
     * 设置额度数据缓存
     */
    setCache(filename, data) {
        this.cache[filename] = {
            data: data,
            timestamp: Date.now()
        };
    },

    /**
     * 清除缓存
     */
    clearCache(filename) {
        if (filename) {
            delete this.cache[filename];
        } else {
            this.cache = {};
        }
    },

    /**
     * 加载额度数据
     */
    async loadQuota(filename, forceRefresh = false) {
        // 检查缓存
        if (!forceRefresh) {
            const cached = this.getCached(filename);
            if (cached) {
                return { success: true, data: cached, fromCache: true };
            }
        }

        try {
            const url = `/antigravity/creds/${encodeURIComponent(filename)}/quotas${forceRefresh ? '?refresh=true' : ''}`;
            const response = await fetch(url, {
                headers: getAuthHeaders()
            });

            const result = await response.json();

            // 处理缓存逻辑
            if (response.ok && result.success && result.data) {
                this.setCache(filename, result.data);
            }

            // 确保返回对象包含 success 字段，如果是 404 等错误，result 可能只包含 detail
            if (!response.ok) {
                return {
                    success: false,
                    message: result.message || result.detail || `Server Error: ${response.status}`
                };
            }

            return result;
        } catch (error) {
            console.error('加载额度失败:', error);
            return {
                success: false,
                message: `网络错误: ${error.message}`
            };
        }
    },

    /**
     * 获取进度条颜色
     */
    getBarColor(percentage) {
        if (percentage > 50) return '#10b981';  // 绿色
        if (percentage > 20) return '#f59e0b';  // 黄色
        return '#ef4444';  // 红色
    },

    /**
     * 获取模型分类图标
     */
    getModelIcon(modelId) {
        const lower = modelId.toLowerCase();
        if (lower.includes('claude')) return '🤖';
        if (lower.includes('gemini')) return '💎';
        return '🔧';
    },

    /**
     * 获取模型简短名称
     */
    getShortName(modelId) {
        return modelId
            .replace('models/', '')
            .replace('publishers/google/', '')
            .split('/').pop();
    },

    /**
     * 渲染额度进度条
     */
    renderQuotaBar(quota) {
        const percentage = quota.remaining * 100;
        const barColor = this.getBarColor(percentage);
        const shortName = this.getShortName(quota.modelId);
        const icon = this.getModelIcon(quota.modelId);

        return `
            <div class="ag-quota-item" title="${quota.modelId} - 重置: ${quota.resetTime}">
                <span class="ag-quota-icon">${icon}</span>
                <span class="ag-quota-name">${shortName}</span>
                <span class="ag-quota-bar">
                    <span style="width:${percentage}%;background:${barColor}"></span>
                </span>
                <span class="ag-quota-pct">${percentage.toFixed(1)}%</span>
                <span class="ag-quota-reset">重置: ${quota.resetTime}</span>
            </div>
        `;
    },

    /**
     * 渲染额度预览 (简略版 - JS Ticker)
     */
    async renderPreview(filename, containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;

        // 清除旧定时器
        if (container.dataset.tickerId) {
            clearTimeout(Number(container.dataset.tickerId));
            delete container.dataset.tickerId;
        }

        const result = await this.loadQuota(filename, false);

        if (!result.success) {
            container.innerHTML = '';
            container.style.display = 'none';
            // 取消注册此预览
            delete this.activePreviews[containerId];
            return;
        }

        const data = result.data.models || {};
        let usedModels = [];
        for (const [id, info] of Object.entries(data)) {
            if (info.remaining < 0.999) usedModels.push({ id, ...info });
        }

        if (usedModels.length === 0) {
            container.innerHTML = '';
            container.style.display = 'none';
            return;
        }

        container.style.display = 'block';

        // 注册此预览实例用于自动刷新
        this.activePreviews[containerId] = filename;
        this.initAutoRefresh();

        usedModels.sort((a, b) => a.remaining - b.remaining);

        // 渲染单项
        const renderItem = (model) => {
            const shortName = this.getShortName(model.id);
            const percentage = (model.remaining * 100).toFixed(0);
            const icon = this.getModelIcon(model.id);
            let color = '#198754';
            if (model.remaining < 0.2) color = '#dc3545';
            else if (model.remaining < 0.5) color = '#ffc107';

            return `
               <div class="ag-quota-ticker-item" title="${shortName}\n剩余: ${(model.remaining * 100).toFixed(1)}%\n重置: ${model.resetTime}">
                   <span class="ag-quota-ticker-icon" style="font-size:12px; opacity:0.8; width:20px;">${icon}</span>
                   <span class="ag-quota-ticker-name" style="font-size:11px; color:#666; font-weight:normal; width:100px; overflow:hidden; text-overflow:ellipsis;">${shortName}</span>
                   <span class="ag-quota-ticker-pct" style="font-size:11px; color:${color}; font-weight:bold; margin-left:5px;">${percentage}%</span>
               </div>
            `;
        };

        const lineHeight = 22; // 固定行高
        let innerHtml = usedModels.map(renderItem).join('');

        // 只有当数量超过3个时才启用轮播
        if (usedModels.length > 3) {
            // 克隆前3个元素放到末尾，实现无缝连接
            const clones = usedModels.slice(0, 3).map(renderItem).join('');
            innerHtml += clones;

            container.innerHTML = `<div class="ag-quota-ticker-wrapper" style="transform: translateY(0);">${innerHtml}</div>`;
            const wrapper = container.querySelector('.ag-quota-ticker-wrapper');

            let currentIndex = 0;
            let isPaused = false; // 鼠标悬停暂停标志
            const totalScrollItems = usedModels.length; // 实际滚动的项目数

            const scrollNext = () => {
                if (isPaused) {
                    // 暂停时不滚动，但继续检查
                    startTimer();
                    return;
                }

                currentIndex++;
                wrapper.style.transition = 'transform 0.8s ease-in-out';
                wrapper.style.transform = `translateY(-${currentIndex * lineHeight}px)`;

                // 检查是否滚动到了克隆区域的末尾 (即原始列表已完全滚出)
                if (currentIndex >= totalScrollItems) {
                    // 等待动画完成后，瞬间重置到顶部
                    setTimeout(() => {
                        wrapper.style.transition = 'none';
                        currentIndex = 0;
                        wrapper.style.transform = `translateY(0)`;

                        // 强制重绘，否则 transition: none 可能不生效
                        wrapper.offsetHeight;

                        // 准备下一次滚动
                        startTimer();
                    }, 850); // 时间要略大于 transition duration
                } else {
                    startTimer();
                }
            };

            const startTimer = () => {
                const tid = setTimeout(scrollNext, 3000); // 停止3秒
                container.dataset.tickerId = String(tid);
            };

            // 鼠标悬停暂停
            container.addEventListener('mouseenter', () => {
                isPaused = true;
            });

            // 鼠标移开恢复
            container.addEventListener('mouseleave', () => {
                isPaused = false;
            });

            startTimer();
        } else {
            // 少于等于3个，直接静态展示，居中
            container.innerHTML = `<div class="ag-quota-ticker-wrapper" style="height:100%; display:flex; flex-direction:column; justify-content:center;">${innerHtml}</div>`;
        }
    },

    /**
     * 显示额度弹窗
     */
    async showQuotaModal(filename, email) {
        // 创建弹窗
        const modal = document.createElement('div');
        modal.className = 'ag-quota-modal';
        modal.id = 'agQuotaModal';
        modal.innerHTML = `
            <div class="ag-quota-modal-content">
                <div class="ag-quota-modal-header">
                    <div class="ag-quota-modal-title">📊 模型额度 - ${email || filename}</div>
                    <button class="ag-quota-modal-close" onclick="AntigravityQuota.closeModal()">&times;</button>
                </div>
                <div class="ag-quota-modal-update-time" id="agQuotaUpdateTime"></div>
                <div class="ag-quota-modal-body" id="agQuotaContent">
                    <div class="ag-quota-loading">加载中...</div>
                </div>
                <div class="ag-quota-modal-footer">
                    <button class="ag-quota-btn ag-quota-btn-refresh" id="agQuotaRefreshBtn" onclick="AntigravityQuota.refreshQuota('${filename}')">🔄 刷新</button>
                    <button class="ag-quota-btn ag-quota-btn-close" onclick="AntigravityQuota.closeModal()">关闭</button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        // 点击遮罩关闭
        modal.onclick = (e) => {
            if (e.target === modal) this.closeModal();
        };

        // 保存当前文件名
        this.currentFilename = filename;

        // 加载数据
        await this.loadAndRenderQuota(filename);
    },

    /**
     * 加载并渲染额度数据
     */
    async loadAndRenderQuota(filename, forceRefresh = false) {
        const contentEl = document.getElementById('agQuotaContent');
        const refreshBtn = document.getElementById('agQuotaRefreshBtn');
        const updateTimeEl = document.getElementById('agQuotaUpdateTime');

        if (!contentEl) return;

        // 禁用刷新按钮
        if (refreshBtn) {
            refreshBtn.disabled = true;
            refreshBtn.textContent = '⏳ 加载中...';
        }

        // 显示加载状态
        contentEl.innerHTML = '<div class="ag-quota-loading">加载中...</div>';

        // 加载数据
        const result = await this.loadQuota(filename, forceRefresh);

        // 恢复刷新按钮
        if (refreshBtn) {
            refreshBtn.disabled = false;
            refreshBtn.textContent = '🔄 刷新';
        }

        if (!result.success) {
            contentEl.innerHTML = `<div class="ag-quota-error">加载失败: ${result.message}</div>`;
            return;
        }

        const data = result.data;

        // 更新时间
        if (updateTimeEl && data.lastUpdated) {
            const updateTime = new Date(data.lastUpdated).toLocaleString('zh-CN', {
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit'
            });
            updateTimeEl.textContent = `更新于 ${updateTime}${result.fromCache ? ' (缓存)' : ''}`;
        }

        // 渲染额度列表
        const models = data.models || {};
        const modelEntries = Object.entries(models);

        if (modelEntries.length === 0) {
            contentEl.innerHTML = '<div class="ag-quota-empty">暂无额度信息</div>';
            return;
        }

        // 按类型分组
        const grouped = {
            claude: [],
            gemini: [],
            other: []
        };

        modelEntries.forEach(([modelId, quota]) => {
            const item = { modelId, ...quota };
            const lower = modelId.toLowerCase();
            if (lower.includes('claude')) {
                grouped.claude.push(item);
            } else if (lower.includes('gemini')) {
                grouped.gemini.push(item);
            } else {
                grouped.other.push(item);
            }
        });

        let html = '';

        // 渲染各组
        if (grouped.claude.length > 0) {
            html += '<div class="ag-quota-group-title">🤖 Claude</div>';
            html += '<div class="ag-quota-group">';
            grouped.claude.forEach(item => {
                html += this.renderQuotaBar(item);
            });
            html += '</div>';
        }

        if (grouped.gemini.length > 0) {
            html += '<div class="ag-quota-group-title">💎 Gemini</div>';
            html += '<div class="ag-quota-group">';
            grouped.gemini.forEach(item => {
                html += this.renderQuotaBar(item);
            });
            html += '</div>';
        }

        if (grouped.other.length > 0) {
            html += '<div class="ag-quota-group-title">🔧 其他</div>';
            html += '<div class="ag-quota-group">';
            grouped.other.forEach(item => {
                html += this.renderQuotaBar(item);
            });
            html += '</div>';
        }

        contentEl.innerHTML = html;
    },

    /**
     * 刷新额度数据
     */
    async refreshQuota(filename) {
        this.clearCache(filename);
        await this.loadAndRenderQuota(filename, true);
    },

    /**
     * 关闭弹窗
     */
    closeModal() {
        const modal = document.getElementById('agQuotaModal');
        if (modal) {
            modal.remove();
        }
    }
};

// 全局暴露
window.AntigravityQuota = AntigravityQuota;
