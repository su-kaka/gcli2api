// =====================================================================
// GCLI2API 控制面板公共JavaScript模块
// =====================================================================

// 基础变量
let currentProjectId = '';
let authInProgress = false;
let uploadSelectedFiles = []; // 上传页面用的文件列表
let authToken = '';
let credsData = {};

// 分页和筛选相关变量
let filteredCredsData = {};
let currentPage = 1;
let pageSize = 20;
let selectedCredFiles = new Set(); // 选中的凭证文件名集合
let totalCredsCount = 0; // 总凭证数量
let currentStatusFilter = 'all'; // 当前状态筛选: all, enabled, disabled
let statsData = {
    total: 0,
    normal: 0,
    disabled: 0
};

// 使用统计相关变量
let usageStatsData = {};
let currentEditingFile = '';

// 配置管理相关变量
let currentConfig = {};
let envLockedFields = new Set();

// 实时日志相关变量
let logWebSocket = null;
let allLogs = [];
let filteredLogs = [];
let currentLogFilter = 'all';

// 冷却倒计时相关变量
let cooldownTimerInterval = null;

// =====================================================================
// 基础函数
// =====================================================================

function showStatus(message, type = 'info') {
    console.log('showStatus called:', message, type);
    const statusSection = document.getElementById('statusSection');
    if (statusSection) {
        statusSection.innerHTML = `<div class="status ${type}">${message}</div>`;
    } else {
        console.error('statusSection not found');
        alert(message); // 临时回退方案
    }
}

// =====================================================================
// 登录相关函数
// =====================================================================

async function login() {
    console.log('Login function called');
    const password = document.getElementById('loginPassword').value;
    console.log('Password length:', password ? password.length : 0);

    if (!password) {
        showStatus('请输入密码', 'error');
        return;
    }

    try {
        console.log('Sending login request...');
        const response = await fetch('./auth/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ password: password })
        });

        console.log('Login response status:', response.status);
        const data = await response.json();
        console.log('Login response data:', data);

        if (response.ok) {
            authToken = data.token;
            // 保存 token 到 localStorage
            localStorage.setItem('gcli2api_auth_token', authToken);
            console.log('Login successful, token received and saved');
            document.getElementById('loginSection').classList.add('hidden');
            document.getElementById('mainSection').classList.remove('hidden');
            showStatus('登录成功', 'success');
        } else {
            console.log('Login failed:', data);
            showStatus(`登录失败: ${data.detail || data.error || '未知错误'}`, 'error');
        }
    } catch (error) {
        console.error('Login error:', error);
        showStatus(`网络错误: ${error.message}`, 'error');
    }
}

// 自动登录函数 - 使用保存的 token
async function autoLogin() {
    const savedToken = localStorage.getItem('gcli2api_auth_token');
    if (!savedToken) {
        console.log('No saved token found');
        return false;
    }

    console.log('Found saved token, attempting auto-login...');
    authToken = savedToken;

    try {
        // 验证 token 是否仍然有效 - 尝试获取配置
        const response = await fetch('./config/get', {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            console.log('Auto-login successful');
            document.getElementById('loginSection').classList.add('hidden');
            document.getElementById('mainSection').classList.remove('hidden');
            showStatus('自动登录成功', 'success');
            return true;
        } else if (response.status === 401) {
            // 只有认证失败（密码错误）时才清除 token
            console.log('Saved token is invalid (401 Unauthorized), clearing...');
            localStorage.removeItem('gcli2api_auth_token');
            authToken = '';
            return false;
        } else {
            // 其他错误（如网络问题、服务器错误）不清除 token
            console.log(`Auto-login failed with status ${response.status}, keeping token for retry`);
            return false;
        }
    } catch (error) {
        // 网络错误不清除 token，保留以便下次重试
        console.error('Auto-login network error:', error);
        console.log('Keeping token for retry due to network error');
        return false;
    }
}

// 退出登录函数
function logout() {
    localStorage.removeItem('gcli2api_auth_token');
    authToken = '';
    document.getElementById('loginSection').classList.remove('hidden');
    document.getElementById('mainSection').classList.add('hidden');
    showStatus('已退出登录', 'info');
    // 清空密码输入框
    const passwordInput = document.getElementById('loginPassword');
    if (passwordInput) {
        passwordInput.value = '';
    }
}

function handlePasswordEnter(event) {
    if (event.key === 'Enter') {
        login();
    }
}

// =====================================================================
// 标签页切换
// =====================================================================

function switchTab(tabName) {
    // 移除所有活动标签
    document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));

    // 激活选中标签
    event.target.classList.add('active');
    document.getElementById(tabName + 'Tab').classList.add('active');

    // 如果切换到文件管理页面，自动加载数据
    if (tabName === 'manage') {
        refreshCredsStatus();
    }
    // 如果切换到配置管理页面，自动加载配置
    if (tabName === 'config') {
        loadConfig();
    }
    // 如果切换到日志页面，自动连接WebSocket
    if (tabName === 'logs') {
        connectWebSocket();
    }
}

// =====================================================================
// 获取认证头
// =====================================================================

function getAuthHeaders() {
    return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
    };
}

// =====================================================================
// OAuth认证相关函数
// =====================================================================

async function startAuth() {
    const projectId = document.getElementById('projectId')?.value?.trim() || '';
    const getAllProjectsEl = document.getElementById('getAllProjectsCreds');
    const getAllProjects = getAllProjectsEl ? getAllProjectsEl.checked : false;
    // 项目ID现在是可选的
    currentProjectId = projectId || null;

    const btn = document.getElementById('getAuthBtn');
    btn.disabled = true;
    btn.textContent = '正在获取认证链接...';

    try {
        const requestBody = {};
        if (projectId) {
            requestBody.project_id = projectId;
        }
        if (getAllProjects) {
            requestBody.get_all_projects = true;
            showStatus('批量并发认证模式：将为当前账号所有项目生成认证链接...', 'info');
        } else if (projectId) {
            showStatus('使用指定的项目ID生成认证链接...', 'info');
        } else {
            showStatus('将尝试自动检测项目ID，正在生成认证链接...', 'info');
        }

        const response = await fetch('./auth/start', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify(requestBody)
        });

        const data = await response.json();

        if (response.ok) {
            document.getElementById('authUrl').href = data.auth_url;
            document.getElementById('authUrl').textContent = data.auth_url;
            document.getElementById('authUrlSection').classList.remove('hidden');

            if (getAllProjects) {
                showStatus('批量并发认证链接已生成，完成授权后将并发为所有可访问项目生成凭证文件', 'info');
            } else if (data.auto_project_detection) {
                showStatus('认证链接已生成（将在认证完成后自动检测项目ID），请点击链接完成授权', 'info');
            } else {
                showStatus(`认证链接已生成（项目ID: ${data.detected_project_id}），请点击链接完成授权`, 'info');
            }
            authInProgress = true;
        } else {
            showStatus(`错误: ${data.error || '获取认证链接失败'}`, 'error');
        }
    } catch (error) {
        showStatus(`网络错误: ${error.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '获取认证链接';
    }
}

async function getCredentials() {
    if (!authInProgress) {
        showStatus('请先获取认证链接并完成授权', 'error');
        return;
    }

    const btn = document.getElementById('getCredsBtn');
    const getAllProjectsEl = document.getElementById('getAllProjectsCreds');
    const getAllProjects = getAllProjectsEl ? getAllProjectsEl.checked : false;
    btn.disabled = true;
    btn.textContent = getAllProjects ? '并发批量获取所有项目凭证中...' : '等待OAuth回调中...';

    try {
        if (getAllProjects) {
            showStatus('正在并发为所有项目获取认证凭证，采用并发处理提升速度...', 'info');
        } else {
            showStatus('正在等待OAuth回调，这可能需要一些时间...', 'info');
        }

        const requestBody = {};
        if (currentProjectId) {
            requestBody.project_id = currentProjectId;
        }
        if (getAllProjects) {
            requestBody.get_all_projects = true;
        }

        const response = await fetch('./auth/callback', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify(requestBody)
        });

        const data = await response.json();

        if (response.ok) {
            const credentialsSection = document.getElementById('credentialsSection');
            const credentialsContent = document.getElementById('credentialsContent');

            if (getAllProjects && data.multiple_credentials) {
                // 处理多项目认证结果
                const results = data.multiple_credentials;
                let resultText = `批量并发认证完成！成功为 ${results.success.length} 个项目生成凭证：\n\n`;

                // 显示成功的项目
                results.success.forEach((item, index) => {
                    resultText += `${index + 1}. 项目: ${item.project_name} (${item.project_id})\n`;
                    resultText += `   文件: ${item.file_path}\n\n`;
                });

                // 显示失败的项目（如果有）
                if (results.failed.length > 0) {
                    resultText += `\n失败的项目 (${results.failed.length} 个):\n`;
                    results.failed.forEach((item, index) => {
                        resultText += `${index + 1}. 项目: ${item.project_name} (${item.project_id})\n`;
                        resultText += `   错误: ${item.error}\n\n`;
                    });
                }

                credentialsContent.textContent = resultText;
                showStatus(`✅ 批量并发认证完成！成功生成 ${results.success.length} 个项目的凭证文件${results.failed.length > 0 ? `，${results.failed.length} 个项目失败` : ''}`, 'success');
            } else {
                // 处理单项目认证结果
                credentialsContent.textContent = JSON.stringify(data.credentials, null, 2);

                if (data.auto_detected_project) {
                    showStatus(`✅ 认证成功！项目ID已自动检测为: ${data.credentials.project_id}，文件已保存到: ${data.file_path}`, 'success');
                } else {
                    showStatus(`✅ 认证成功！文件已保存到: ${data.file_path}`, 'success');
                }
            }

            credentialsSection.classList.remove('hidden');
            authInProgress = false;
        } else {
            // 检查是否需要项目选择
            if (data.requires_project_selection && data.available_projects) {
                let projectOptions = "请选择一个项目：\n\n";
                data.available_projects.forEach((project, index) => {
                    projectOptions += `${index + 1}. ${project.name} (${project.projectId})\n`;
                });
                projectOptions += `\n请输入序号 (1-${data.available_projects.length}):`;

                const selection = prompt(projectOptions);
                const projectIndex = parseInt(selection) - 1;

                if (projectIndex >= 0 && projectIndex < data.available_projects.length) {
                    const selectedProject = data.available_projects[projectIndex];
                    currentProjectId = selectedProject.projectId;
                    btn.textContent = '重新尝试获取认证文件';
                    showStatus(`使用选择的项目 ${selectedProject.name} (${selectedProject.projectId}) 重新尝试...`, 'info');
                    setTimeout(() => getCredentials(), 1000);
                    return;
                } else {
                    showStatus('无效的选择，请重新开始认证', 'error');
                }
            }
            // 检查是否需要手动输入项目ID
            else if (data.requires_manual_project_id) {
                const userProjectId = prompt('无法自动检测项目ID，请手动输入您的Google Cloud项目ID:');
                if (userProjectId && userProjectId.trim()) {
                    // 重新尝试，使用用户输入的项目ID
                    currentProjectId = userProjectId.trim();
                    btn.textContent = '重新尝试获取认证文件';
                    showStatus('使用手动输入的项目ID重新尝试...', 'info');
                    setTimeout(() => getCredentials(), 1000);
                    return;
                } else {
                    showStatus('需要项目ID才能完成认证，请重新开始并输入正确的项目ID', 'error');
                }
            } else {
                showStatus(`❌ 错误: ${data.error || '获取认证文件失败'}`, 'error');
                if (data.error && data.error.includes('未接收到授权回调')) {
                    showStatus('提示：请确保已完成浏览器中的OAuth认证，并看到了"OAuth authentication successful"页面', 'info');
                }
            }
        }
    } catch (error) {
        showStatus(`网络错误: ${error.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '获取认证文件';
    }
}

// Project ID 折叠切换函数
function toggleProjectIdSection() {
    const section = document.getElementById('projectIdSection');
    const icon = document.getElementById('projectIdToggleIcon');

    if (section.style.display === 'none') {
        section.style.display = 'block';
        icon.style.transform = 'rotate(90deg)';
        icon.textContent = '▼';
    } else {
        section.style.display = 'none';
        icon.style.transform = 'rotate(0deg)';
        icon.textContent = '▶';
    }
}

// 回调URL输入区域折叠切换函数
function toggleCallbackUrlSection() {
    const section = document.getElementById('callbackUrlSection');
    const icon = document.getElementById('callbackUrlToggleIcon');

    if (section.style.display === 'none') {
        section.style.display = 'block';
        icon.style.transform = 'rotate(180deg)';
        icon.textContent = '▲';
    } else {
        section.style.display = 'none';
        icon.style.transform = 'rotate(0deg)';
        icon.textContent = '▼';
    }
}

// 处理回调URL的函数
async function processCallbackUrl() {
    const callbackUrlInput = document.getElementById('callbackUrlInput');
    const callbackUrl = callbackUrlInput.value.trim();
    const getAllProjectsEl = document.getElementById('getAllProjectsCreds');
    const getAllProjects = getAllProjectsEl ? getAllProjectsEl.checked : false;

    if (!callbackUrl) {
        showStatus('请输入回调URL', 'error');
        return;
    }

    // 简单验证URL格式
    if (!callbackUrl.startsWith('http://') && !callbackUrl.startsWith('https://')) {
        showStatus('请输入有效的URL（以http://或https://开头）', 'error');
        return;
    }

    // 检查是否包含必要参数
    if (!callbackUrl.includes('code=') || !callbackUrl.includes('state=')) {
        showStatus('❌ 这不是有效的回调URL！请确保：\n1. 已完成Google OAuth授权\n2. 复制的是浏览器地址栏的完整URL\n3. URL包含code和state参数', 'error');
        return;
    }

    if (getAllProjects) {
        showStatus('正在从回调URL并发批量获取所有项目凭证...', 'info');
    } else {
        showStatus('正在从回调URL获取凭证...', 'info');
    }

    try {
        // 获取当前项目ID设置（如果有的话）
        const projectIdInput = document.getElementById('projectId');
        const projectId = projectIdInput ? projectIdInput.value.trim() : null;

        const response = await fetch('./auth/callback-url', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({
                callback_url: callbackUrl,
                project_id: projectId || null,
                get_all_projects: getAllProjects
            })
        });

        const result = await response.json();

        if (getAllProjects && result.multiple_credentials) {
            // 处理多项目认证结果
            const results = result.multiple_credentials;
            let resultText = `批量并发认证完成！成功为 ${results.success.length} 个项目生成凭证：\n\n`;

            // 显示成功的项目
            results.success.forEach((item, index) => {
                resultText += `${index + 1}. 项目: ${item.project_name} (${item.project_id})\n`;
                resultText += `   文件: ${item.file_path}\n\n`;
            });

            // 显示失败的项目（如果有）
            if (results.failed.length > 0) {
                resultText += `\n失败的项目 (${results.failed.length} 个):\n`;
                results.failed.forEach((item, index) => {
                    resultText += `${index + 1}. 项目: ${item.project_name} (${item.project_id})\n`;
                    resultText += `   错误: ${item.error}\n\n`;
                });
            }

            // 显示结果
            document.getElementById('credentialsContent').textContent = resultText;
            document.getElementById('credentialsSection').classList.remove('hidden');
            showStatus(`✅ 批量并发认证完成！成功生成 ${results.success.length} 个项目的凭证文件${results.failed.length > 0 ? `，${results.failed.length} 个项目失败` : ''}`, 'success');

        } else if (result.credentials) {
            // 处理单项目认证结果
            showStatus(result.message || '从回调URL获取凭证成功！', 'success');

            // 显示凭证内容
            document.getElementById('credentialsContent').innerHTML =
                '<pre>' + JSON.stringify(result.credentials, null, 2) + '</pre>';
            document.getElementById('credentialsSection').classList.remove('hidden');

        } else if (result.requires_manual_project_id) {
            showStatus('需要手动指定项目ID，请在高级选项中填入Google Cloud项目ID后重试', 'error');
        } else if (result.requires_project_selection) {
            let projectOptions = '<br><strong>可用项目：</strong><br>';
            result.available_projects.forEach(project => {
                projectOptions += `• ${project.name} (ID: ${project.projectId})<br>`;
            });
            showStatus('检测到多个项目，请在高级选项中指定项目ID：' + projectOptions, 'error');
        } else {
            showStatus(result.error || '从回调URL获取凭证失败', 'error');
        }

        // 清空输入框
        callbackUrlInput.value = '';

        // 刷新凭证列表（如果有）
        setTimeout(() => {
            if (typeof refreshCredsStatus === 'function') {
                refreshCredsStatus();
            }
        }, 1000);

    } catch (error) {
        console.error('从回调URL获取凭证时出错:', error);
        showStatus(`从回调URL获取凭证失败: ${error.message}`, 'error');
    }
}

// 处理勾选框状态变化
function handleGetAllProjectsChange() {
    const checkbox = document.getElementById('getAllProjectsCreds');
    const note = document.getElementById('allProjectsNote');
    const projectIdSection = document.getElementById('projectIdSection');
    const projectIdToggle = document.querySelector('[onclick="toggleProjectIdSection()"]');

    if (checkbox.checked) {
        // 显示批量认证提示
        note.style.display = 'block';
        // 禁用项目ID输入（批量模式下不需要指定单个项目）
        if (projectIdSection.style.display !== 'none') {
            toggleProjectIdSection();
        }
        projectIdToggle.style.opacity = '0.5';
        projectIdToggle.style.pointerEvents = 'none';
        projectIdToggle.title = '批量认证模式下无需指定单个项目ID';
    } else {
        // 隐藏批量认证提示
        note.style.display = 'none';
        // 重新启用项目ID输入
        projectIdToggle.style.opacity = '1';
        projectIdToggle.style.pointerEvents = 'auto';
        projectIdToggle.title = '';
    }
}

// =====================================================================
// 文件上传相关函数
// =====================================================================

function handleFileSelect(event) {
    const files = Array.from(event.target.files);
    addFiles(files);
}

function addFiles(files) {
    files.forEach(file => {
        if (file.type === 'application/json' || file.name.endsWith('.json') ||
            file.type === 'application/zip' || file.name.endsWith('.zip')) {
            if (!uploadSelectedFiles.find(f => f.name === file.name && f.size === file.size)) {
                uploadSelectedFiles.push(file);
            }
        } else {
            showStatus(`文件 ${file.name} 格式不支持，只支持JSON和ZIP文件`, 'error');
        }
    });

    updateFileList();
}

function updateFileList() {
    const fileList = document.getElementById('fileList');
    const fileListSection = document.getElementById('fileListSection');

    if (uploadSelectedFiles.length === 0) {
        fileListSection.classList.add('hidden');
        return;
    }

    fileListSection.classList.remove('hidden');
    fileList.innerHTML = '';

    uploadSelectedFiles.forEach((file, index) => {
        const fileItem = document.createElement('div');
        fileItem.className = 'file-item';
        const isZip = file.name.endsWith('.zip');
        const fileIcon = isZip ? '📦' : '📄';
        const fileType = isZip ? ' (ZIP压缩包)' : ' (JSON文件)';
        fileItem.innerHTML = `
            <div>
                <span class="file-name">${fileIcon} ${file.name}</span>
                <span class="file-size">(${formatFileSize(file.size)}${fileType})</span>
            </div>
            <button class="remove-btn" onclick="removeFile(${index})">删除</button>
        `;
        fileList.appendChild(fileItem);
    });
}

function removeFile(index) {
    uploadSelectedFiles.splice(index, 1);
    updateFileList();
}

function clearFiles() {
    uploadSelectedFiles = [];
    updateFileList();
}

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + ' KB';
    return Math.round(bytes / (1024 * 1024)) + ' MB';
}

async function uploadFiles() {
    if (uploadSelectedFiles.length === 0) {
        showStatus('请选择要上传的文件', 'error');
        return;
    }

    const progressSection = document.getElementById('uploadProgressSection');
    const progressFill = document.getElementById('progressFill');
    const progressText = document.getElementById('progressText');

    progressSection.classList.remove('hidden');

    const formData = new FormData();
    uploadSelectedFiles.forEach(file => {
        formData.append('files', file);
    });

    // 检查是否有ZIP文件，给用户提示
    const hasZipFiles = uploadSelectedFiles.some(file => file.name.endsWith('.zip'));
    if (hasZipFiles) {
        showStatus('正在上传并解压ZIP文件...', 'info');
    }

    try {
        const xhr = new XMLHttpRequest();

        // 设置超时时间 (5分钟)
        xhr.timeout = 300000;

        xhr.upload.onprogress = function (event) {
            if (event.lengthComputable) {
                const percentComplete = (event.loaded / event.total) * 100;
                progressFill.style.width = percentComplete + '%';
                progressText.textContent = Math.round(percentComplete) + '%';
            }
        };

        xhr.onload = function () {
            if (xhr.status === 200) {
                try {
                    const data = JSON.parse(xhr.responseText);
                    showStatus(`成功上传 ${data.uploaded_count} 个文件`, 'success');
                    clearFiles();
                    progressSection.classList.add('hidden');
                } catch (e) {
                    showStatus('上传失败: 服务器响应格式错误', 'error');
                }
            } else {
                try {
                    const error = JSON.parse(xhr.responseText);
                    showStatus(`上传失败: ${error.detail || error.error || '未知错误'}`, 'error');
                } catch (e) {
                    showStatus(`上传失败: HTTP ${xhr.status} - ${xhr.statusText || '未知错误'}`, 'error');
                }
            }
        };

        xhr.onerror = function () {
            const totalSize = uploadSelectedFiles.reduce((sum, file) => sum + file.size, 0);
            console.error('Upload XHR error:', {
                readyState: xhr.readyState,
                status: xhr.status,
                statusText: xhr.statusText,
                responseText: xhr.responseText,
                fileCount: uploadSelectedFiles.length,
                totalSize: (totalSize / 1024 / 1024).toFixed(1) + 'MB'
            });
            showStatus(`上传失败：连接中断 - 可能原因：文件过多(${uploadSelectedFiles.length}个)或网络不稳定。建议分批上传。`, 'error');
            progressSection.classList.add('hidden');
        };

        xhr.ontimeout = function () {
            showStatus('上传失败：请求超时 - 文件处理时间过长，请减少文件数量或检查网络连接', 'error');
            progressSection.classList.add('hidden');
        };

        xhr.open('POST', '/auth/upload');
        xhr.setRequestHeader('Authorization', `Bearer ${authToken}`);
        xhr.send(formData);

    } catch (error) {
        showStatus(`上传失败: ${error.message}`, 'error');
    }
}

// =====================================================================
// WebSocket日志相关变量和函数
// =====================================================================

function connectWebSocket() {
    if (logWebSocket && logWebSocket.readyState === WebSocket.OPEN) {
        showStatus('WebSocket已经连接', 'info');
        return;
    }

    try {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsPath = new URL('./auth/logs/stream', window.location.href).href;
        const wsUrl = wsPath.replace(/^http/, 'ws');

        document.getElementById('connectionStatusText').textContent = '连接中...';
        document.getElementById('logConnectionStatus').className = 'status info';

        logWebSocket = new WebSocket(wsUrl);

        logWebSocket.onopen = function (event) {
            document.getElementById('connectionStatusText').textContent = '已连接';
            document.getElementById('logConnectionStatus').className = 'status success';
            showStatus('日志流连接成功', 'success');
            clearLogsDisplay(); // 只清空前端显示的旧日志，不清空服务器文件
        };

        logWebSocket.onmessage = function (event) {
            const logLine = event.data;
            if (logLine.trim()) {
                allLogs.push(logLine);

                // 限制日志数量，保留最后1000条
                if (allLogs.length > 1000) {
                    allLogs = allLogs.slice(-1000);
                }

                filterLogs();

                // 自动滚动到底部
                if (document.getElementById('autoScroll').checked) {
                    const logContainer = document.getElementById('logContainer');
                    logContainer.scrollTop = logContainer.scrollHeight;
                }
            }
        };

        logWebSocket.onclose = function (event) {
            document.getElementById('connectionStatusText').textContent = '连接断开';
            document.getElementById('logConnectionStatus').className = 'status error';
            showStatus('日志流连接断开', 'info');
        };

        logWebSocket.onerror = function (error) {
            document.getElementById('connectionStatusText').textContent = '连接错误';
            document.getElementById('logConnectionStatus').className = 'status error';
            showStatus('日志流连接错误: ' + error, 'error');
        };

    } catch (error) {
        showStatus('创建WebSocket连接失败: ' + error.message, 'error');
        document.getElementById('connectionStatusText').textContent = '连接失败';
        document.getElementById('logConnectionStatus').className = 'status error';
    }
}

function disconnectWebSocket() {
    if (logWebSocket) {
        logWebSocket.close();
        logWebSocket = null;
        document.getElementById('connectionStatusText').textContent = '未连接';
        document.getElementById('logConnectionStatus').className = 'status info';
        showStatus('日志流连接已断开', 'info');
    }
}

function clearLogsDisplay() {
    // 只清空前端显示的日志，不清空服务器文件
    allLogs = [];
    filteredLogs = [];
    document.getElementById('logContent').textContent = '日志已清空，等待新日志...';
}

async function downloadLogs() {
    try {
        // 调用后端API下载日志文件
        const response = await fetch('./auth/logs/download', {
            method: 'GET',
            headers: getAuthHeaders()
        });

        if (response.ok) {
            // 获取文件名
            const contentDisposition = response.headers.get('Content-Disposition');
            let filename = 'gcli2api_logs.txt';
            if (contentDisposition) {
                const filenameMatch = contentDisposition.match(/filename=(.+)/);
                if (filenameMatch) {
                    filename = filenameMatch[1];
                }
            }

            // 下载文件
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);

            showStatus(`日志文件下载成功: ${filename}`, 'success');
        } else {
            const errorText = await response.text();
            let errorMsg = '下载失败';
            try {
                const errorData = JSON.parse(errorText);
                errorMsg = errorData.detail || errorData.error || '未知错误';
            } catch (e) {
                errorMsg = errorText || '未知错误';
            }
            showStatus(`下载日志失败: ${errorMsg}`, 'error');
        }
    } catch (error) {
        console.error('downloadLogs error:', error);
        showStatus(`下载日志时网络错误: ${error.message}`, 'error');
    }
}

async function clearLogs() {
    try {
        // 调用后端API清空日志文件
        const response = await fetch('./auth/logs/clear', {
            method: 'POST',
            headers: getAuthHeaders()
        });

        const data = await response.json();

        if (response.ok) {
            // 清空前端显示的日志
            clearLogsDisplay();
            showStatus(data.message, 'success');
        } else {
            showStatus(`清空日志失败: ${data.detail || data.error || '未知错误'}`, 'error');
        }
    } catch (error) {
        console.error('clearLogs error:', error);
        // 即使后端清空失败，也清空前端显示
        clearLogsDisplay();
        showStatus(`清空日志时网络错误: ${error.message}`, 'error');
    }
}

function filterLogs() {
    const filter = document.getElementById('logLevelFilter').value;
    currentLogFilter = filter;

    if (filter === 'all') {
        filteredLogs = [...allLogs];
    } else {
        filteredLogs = allLogs.filter(log => log.toUpperCase().includes(filter));
    }

    displayLogs();
}

function displayLogs() {
    const logContent = document.getElementById('logContent');
    if (filteredLogs.length === 0) {
        logContent.textContent = currentLogFilter === 'all' ?
            '暂无日志...' : `暂无${currentLogFilter}级别的日志...`;
    } else {
        logContent.textContent = filteredLogs.join('\n');
    }
}

// =====================================================================
// 凭证文件管理相关函数
// =====================================================================

async function refreshCredsStatus() {
    const credsLoading = document.getElementById('credsLoading');
    const credsList = document.getElementById('credsList');

    try {
        credsLoading.style.display = 'block';
        credsList.innerHTML = '';

        console.log('Fetching creds status...');

        // 构建分页和筛选参数
        const offset = (currentPage - 1) * pageSize;
        const statusFilter = currentStatusFilter;
        const response = await fetch(`./creds/status?offset=${offset}&limit=${pageSize}&status_filter=${statusFilter}`, {
            method: 'GET',
            headers: getAuthHeaders()
        });

        console.log('Creds status response:', response.status);

        const data = await response.json();
        console.log('Creds status data:', data);

        if (response.ok) {
            // 新API返回 {items, total, offset, limit, has_more}
            // 转换为旧的 credsData 格式以兼容现有代码
            credsData = {};
            for (const item of data.items) {
                const filename = item.filename;
                credsData[filename] = {
                    filename: filename,
                    status: {
                        disabled: item.disabled,
                        error_codes: item.error_codes || [],
                        last_success: item.last_success,
                    },
                    user_email: item.user_email,
                    cooldown_status: item.cooldown_status,
                    cooldown_remaining_seconds: item.cooldown_remaining_seconds,
                    cooldown_until: item.cooldown_until
                };
            }

            // 保存总数用于分页（这是筛选后的总数）
            totalCredsCount = data.total;

            // 计算统计数据（基于当前页）
            calculateStats();

            // 更新统计显示
            updateStatsDisplay();

            // 直接显示数据，不再前端筛选
            filteredCredsData = credsData;
            renderCredsList();
            updatePagination();

            // 更新状态消息
            let statusMsg = `已加载 ${data.total} 个凭证文件`;
            if (statusFilter === 'enabled') {
                statusMsg += ' (筛选: 仅启用)';
            } else if (statusFilter === 'disabled') {
                statusMsg += ' (筛选: 仅禁用)';
            }
            showStatus(statusMsg, 'success');
        } else {
            showStatus(`加载失败: ${data.detail || data.error || '未知错误'}`, 'error');
        }
    } catch (error) {
        console.error('refreshCredsStatus error:', error);
        showStatus(`网络错误: ${error.message}`, 'error');
    } finally {
        credsLoading.style.display = 'none';
    }
}

// 应用状态筛选
function applyStatusFilter() {
    const statusFilter = document.getElementById('statusFilter').value;
    currentStatusFilter = statusFilter;
    currentPage = 1; // 重置到第一页
    refreshCredsStatus(); // 重新从服务器获取数据
}

// 计算统计数据（基于当前页数据）
function calculateStats() {
    statsData = {
        total: totalCredsCount, // 使用服务器返回的总数
        normal: 0,
        disabled: 0
    };

    // 基于当前页数据统计
    for (const [fullPath, credInfo] of Object.entries(credsData)) {
        if (credInfo.status.disabled) {
            statsData.disabled++;
        } else {
            statsData.normal++;
        }
    }
}

// 更新统计显示
function updateStatsDisplay() {
    document.getElementById('statTotal').textContent = statsData.total;
    document.getElementById('statNormal').textContent = statsData.normal;
    document.getElementById('statDisabled').textContent = statsData.disabled;
}

// 获取总页数
function getTotalPages() {
    return Math.ceil(totalCredsCount / pageSize);
}

// 渲染凭证列表
function renderCredsList() {
    const credsList = document.getElementById('credsList');
    credsList.innerHTML = '';

    const currentPageData = Object.entries(filteredCredsData);

    if (currentPageData.length === 0) {
        const message = totalCredsCount === 0 ?
            '暂无凭证文件' : '当前筛选条件下暂无数据';
        credsList.innerHTML = `<p style="text-align: center; color: #666;">${message}</p>`;
        document.getElementById('paginationContainer').style.display = 'none';
        return;
    }

    for (const [fullPath, credInfo] of currentPageData) {
        const card = createCredCard(fullPath, credInfo);
        credsList.appendChild(card);
    }

    document.getElementById('paginationContainer').style.display = getTotalPages() > 1 ? 'flex' : 'none';

    // 更新批量控件状态
    updateBatchControls();
}

// 更新分页信息
function updatePagination() {
    const totalPages = getTotalPages();
    const startItem = (currentPage - 1) * pageSize + 1;
    const endItem = Math.min(currentPage * pageSize, totalCredsCount);

    document.getElementById('paginationInfo').textContent =
        `第 ${currentPage} 页，共 ${totalPages} 页 (显示 ${startItem}-${endItem}，共 ${totalCredsCount} 项)`;

    document.getElementById('prevPageBtn').disabled = currentPage <= 1;
    document.getElementById('nextPageBtn').disabled = currentPage >= totalPages;
}

// 切换页面
function changePage(direction) {
    const totalPages = getTotalPages();
    const newPage = currentPage + direction;

    if (newPage >= 1 && newPage <= totalPages) {
        currentPage = newPage;
        refreshCredsStatus(); // 重新加载新页数据
    }
}

// 改变每页显示数量
function changePageSize() {
    pageSize = parseInt(document.getElementById('pageSizeSelect').value);
    currentPage = 1;
    refreshCredsStatus(); // 重新加载数据
}

function createCredCard(fullPath, credInfo) {
    const div = document.createElement('div');
    const status = credInfo.status;
    const filename = credInfo.filename;

    // 调试：记录状态
    if (filename.includes('atomic-affinity')) {
        console.log(`Creating card for ${filename}:`, status);
    }

    // 设置卡片状态样式
    let cardClass = 'cred-card';
    if (status.disabled) cardClass += ' disabled';

    div.className = cardClass;

    // 创建状态标签
    let statusBadges = '';
    if (status.disabled) {
        statusBadges += '<span class="status-badge disabled">已禁用</span>';
    } else {
        statusBadges += '<span class="status-badge enabled">已启用</span>';
    }

    // 调试:记录 error_codes
    console.log(`Error codes for ${filename}:`, status.error_codes);

    if (status.error_codes && status.error_codes.length > 0) {
        statusBadges += `<span class="error-codes">错误码: ${status.error_codes.join(', ')}</span>`;
        // 检查是否包含自动封禁的错误码
        const autoBanErrors = status.error_codes.filter(code => code === 400 || code === 403);
        if (autoBanErrors.length > 0 && status.disabled) {
            statusBadges += `<span class="status-badge" style="background-color: #e74c3c; color: white;">AUTO_BAN</span>`;
        }
    } else {
        // 显示无错误码状态
        statusBadges += `<span class="status-badge" style="background-color: #28a745; color: white;">无错误</span>`;
    }

    // 添加冷却状态显示
    if (credInfo.cooldown_status === 'cooling' && credInfo.cooldown_remaining_seconds) {
        const remainingSeconds = credInfo.cooldown_remaining_seconds;
        const hours = Math.floor(remainingSeconds / 3600);
        const minutes = Math.floor((remainingSeconds % 3600) / 60);
        const seconds = remainingSeconds % 60;

        let timeDisplay = '';
        if (hours > 0) {
            timeDisplay = `${hours}h ${minutes}m ${seconds}s`;
        } else if (minutes > 0) {
            timeDisplay = `${minutes}m ${seconds}s`;
        } else {
            timeDisplay = `${seconds}s`;
        }

        statusBadges += `<span class="cooldown-badge" title="冷却截止时间: ${new Date(credInfo.cooldown_until * 1000).toLocaleString('zh-CN')}">🕐 冷却中: ${timeDisplay}</span>`;
    }

    // 为HTML ID生成安全的标识符
    const pathId = btoa(encodeURIComponent(fullPath)).replace(/[+/=]/g, '_');

    // 创建操作按钮 - 使用文件名而不是完整路径
    let actionButtons = '';
    if (status.disabled) {
        actionButtons += `<button class="cred-btn enable" data-filename="${filename}" data-action="enable">启用</button>`;
    } else {
        actionButtons += `<button class="cred-btn disable" data-filename="${filename}" data-action="disable">禁用</button>`;
    }

    actionButtons += `
        <button class="cred-btn view" onclick="toggleCredDetails('${pathId}')">查看内容</button>
        <button class="cred-btn download" onclick="downloadCred('${filename}')">下载</button>
        <button class="cred-btn email" onclick="fetchUserEmail('${filename}')">查看账号邮箱</button>
        <button class="cred-btn delete" data-filename="${filename}" data-action="delete">删除</button>
    `;

    // 构建邮箱显示
    let emailInfo = '';
    if (credInfo.user_email) {
        emailInfo = `<div class="cred-email" style="font-size: 12px; color: #666; margin-top: 2px;">${credInfo.user_email}</div>`;
    } else {
        emailInfo = `<div class="cred-email" style="font-size: 12px; color: #999; margin-top: 2px; font-style: italic;">未获取邮箱</div>`;
    }

    div.innerHTML = `
        <div class="cred-header">
            <div style="display: flex; align-items: center; gap: 10px;">
                <input type="checkbox" class="file-checkbox" data-filename="${filename}" onchange="toggleFileSelection('${filename}')">
                <div>
                    <div class="cred-filename">${filename}</div>
                    ${emailInfo}
                </div>
            </div>
            <div class="cred-status">${statusBadges}</div>
        </div>
        <div class="cred-actions">${actionButtons}</div>
        <div class="cred-details" id="details-${pathId}">
            <div class="cred-content"></div>
        </div>
    `;

    // 设置文件内容（避免HTML注入）
    const contentDiv = div.querySelector('.cred-content');
    // 初始显示加载提示
    contentDiv.textContent = '点击"查看内容"按钮加载文件详情...';
    contentDiv.setAttribute('data-filename', filename);
    contentDiv.setAttribute('data-loaded', 'false');

    // 添加事件监听器到按钮
    const actionButtonElements = div.querySelectorAll('[data-filename][data-action]');
    actionButtonElements.forEach(button => {
        button.addEventListener('click', function () {
            const filename = this.getAttribute('data-filename');
            const action = this.getAttribute('data-action');

            if (action === 'delete') {
                deleteCred(filename);
            } else {
                credAction(filename, action);
            }
        });
    });

    return div;
}

async function credAction(filename, action) {
    try {
        console.log('Performing action:', action, 'on file:', filename);
        console.log('Filename type:', typeof filename);
        console.log('Filename length:', filename.length);
        console.log('Ends with .json:', filename.endsWith('.json'));

        const requestBody = {
            filename: filename,
            action: action
        };

        console.log('Request body:', requestBody);

        const response = await fetch('./creds/action', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify(requestBody)
        });

        console.log('Response status:', response.status);

        const data = await response.json();
        console.log('Response data:', data);

        if (response.ok) {
            showStatus(data.message, 'success');
            await refreshCredsStatus(); // 刷新状态
        } else {
            showStatus(`操作失败: ${data.detail || data.error || '未知错误'}`, 'error');
        }
    } catch (error) {
        console.error('credAction error:', error);
        showStatus(`网络错误: ${error.message}`, 'error');
    }
}

async function toggleCredDetails(pathId) {
    const detailsId = 'details-' + pathId;
    const details = document.getElementById(detailsId);
    if (!details) return;

    // 切换显示状态
    const isShowing = details.classList.toggle('show');

    // 如果是展开且内容未加载,则加载内容
    if (isShowing) {
        const contentDiv = details.querySelector('.cred-content');
        const filename = contentDiv.getAttribute('data-filename');
        const loaded = contentDiv.getAttribute('data-loaded');

        if (loaded === 'false' && filename) {
            // 显示加载中
            contentDiv.textContent = '正在加载文件内容...';

            try {
                // 从服务器获取完整内容
                const response = await fetch(`./creds/detail/${encodeURIComponent(filename)}`, {
                    method: 'GET',
                    headers: getAuthHeaders()
                });

                const data = await response.json();

                if (response.ok && data.content) {
                    contentDiv.textContent = JSON.stringify(data.content, null, 2);
                    contentDiv.setAttribute('data-loaded', 'true');
                } else {
                    contentDiv.textContent = '无法加载文件内容: ' + (data.error || data.detail || '未知错误');
                }
            } catch (error) {
                contentDiv.textContent = '加载文件内容失败: ' + error.message;
            }
        }
    }
}

async function downloadCred(filename) {
    try {
        const response = await fetch(`./creds/download/${filename}`, {
            method: 'GET',
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
            showStatus(`已下载文件: ${filename}`, 'success');
        } else {
            const data = await response.json();
            showStatus(`下载失败: ${data.error}`, 'error');
        }
    } catch (error) {
        showStatus(`下载失败: ${error.message}`, 'error');
    }
}

async function downloadAllCreds() {
    try {
        const response = await fetch('./creds/download-all', {
            method: 'GET',
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = 'credentials.zip';
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
            showStatus('已下载所有凭证文件', 'success');
        } else {
            const data = await response.json();
            showStatus(`打包下载失败: ${data.error}`, 'error');
        }
    } catch (error) {
        showStatus(`打包下载失败: ${error.message}`, 'error');
    }
}

async function deleteCred(filename) {
    if (!confirm(`确定要删除凭证文件吗？\n${filename}`)) {
        return;
    }

    await credAction(filename, 'delete');
}

// =====================================================================
// 批量操作相关函数
// =====================================================================

function toggleFileSelection(filename) {
    if (selectedCredFiles.has(filename)) {
        selectedCredFiles.delete(filename);
    } else {
        selectedCredFiles.add(filename);
    }
    updateBatchControls();
}

function toggleSelectAll() {
    const selectAllCheckbox = document.getElementById('selectAllCheckbox');
    const fileCheckboxes = document.querySelectorAll('.file-checkbox');

    if (selectAllCheckbox.checked) {
        // 全选当前页面的文件
        fileCheckboxes.forEach(checkbox => {
            const filename = checkbox.getAttribute('data-filename');
            selectedCredFiles.add(filename);
            checkbox.checked = true;
        });
    } else {
        // 取消全选
        selectedCredFiles.clear();
        fileCheckboxes.forEach(checkbox => {
            checkbox.checked = false;
        });
    }
    updateBatchControls();
}

function updateBatchControls() {
    const selectedCount = selectedCredFiles.size;
    const selectedCountElement = document.getElementById('selectedCount');
    const batchEnableBtn = document.getElementById('batchEnableBtn');
    const batchDisableBtn = document.getElementById('batchDisableBtn');
    const batchDeleteBtn = document.getElementById('batchDeleteBtn');
    const selectAllCheckbox = document.getElementById('selectAllCheckbox');

    selectedCountElement.textContent = `已选择 ${selectedCount} 项`;

    // 启用/禁用批量操作按钮
    const hasSelection = selectedCount > 0;
    batchEnableBtn.disabled = !hasSelection;
    batchDisableBtn.disabled = !hasSelection;
    batchDeleteBtn.disabled = !hasSelection;

    // 更新全选复选框状态
    const currentPageFileCount = document.querySelectorAll('.file-checkbox').length;
    const currentPageSelectedCount = Array.from(document.querySelectorAll('.file-checkbox'))
        .filter(checkbox => selectedCredFiles.has(checkbox.getAttribute('data-filename'))).length;

    if (currentPageSelectedCount === 0) {
        selectAllCheckbox.indeterminate = false;
        selectAllCheckbox.checked = false;
    } else if (currentPageSelectedCount === currentPageFileCount) {
        selectAllCheckbox.indeterminate = false;
        selectAllCheckbox.checked = true;
    } else {
        selectAllCheckbox.indeterminate = true;
        selectAllCheckbox.checked = false;
    }

    // 更新页面上的复选框状态
    document.querySelectorAll('.file-checkbox').forEach(checkbox => {
        const filename = checkbox.getAttribute('data-filename');
        checkbox.checked = selectedCredFiles.has(filename);
    });
}

async function batchAction(action) {
    const selectedFiles = Array.from(selectedCredFiles);

    if (selectedFiles.length === 0) {
        showStatus('请先选择要操作的文件', 'error');
        return;
    }

    let confirmMessage = '';
    switch (action) {
        case 'enable':
            confirmMessage = `确定要启用选中的 ${selectedFiles.length} 个文件吗？`;
            break;
        case 'disable':
            confirmMessage = `确定要禁用选中的 ${selectedFiles.length} 个文件吗？`;
            break;
        case 'delete':
            confirmMessage = `确定要删除选中的 ${selectedFiles.length} 个文件吗？\n注意：此操作不可恢复！`;
            break;
    }

    if (!confirm(confirmMessage)) {
        return;
    }

    try {
        showStatus(`正在执行批量${action === 'enable' ? '启用' : action === 'disable' ? '禁用' : '删除'}操作...`, 'info');

        const requestBody = {
            action: action,
            filenames: selectedFiles
        };

        const response = await fetch('./creds/batch-action', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify(requestBody)
        });

        const data = await response.json();

        if (response.ok) {
            showStatus(`批量操作完成：成功处理 ${data.success_count}/${selectedFiles.length} 个文件`, 'success');

            // 清空选择
            selectedCredFiles.clear();
            updateBatchControls();

            // 刷新列表
            await refreshCredsStatus();
        } else {
            showStatus(`批量操作失败: ${data.detail || data.error || '未知错误'}`, 'error');
        }
    } catch (error) {
        console.error('batchAction error:', error);
        showStatus(`批量操作网络错误: ${error.message}`, 'error');
    }
}

// =====================================================================
// 邮箱相关函数
// =====================================================================

async function fetchUserEmail(filename) {
    try {
        showStatus('正在获取用户邮箱...', 'info');

        const response = await fetch(`./creds/fetch-email/${encodeURIComponent(filename)}`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            }
        });

        const data = await response.json();

        if (response.ok && data.user_email) {
            showStatus(`成功获取邮箱: ${data.user_email}`, 'success');
            // 刷新凭证状态以更新显示
            await refreshCredsStatus();
        } else {
            showStatus(data.message || '无法获取用户邮箱', 'error');
        }
    } catch (error) {
        console.error('fetchUserEmail error:', error);
        showStatus(`获取邮箱失败: ${error.message}`, 'error');
    }
}

async function refreshAllEmails() {
    try {
        if (!confirm('确定要刷新所有凭证的用户邮箱吗？这可能需要一些时间。')) {
            return;
        }

        showStatus('正在刷新所有用户邮箱...', 'info');

        const response = await fetch('./creds/refresh-all-emails', {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            }
        });

        const data = await response.json();

        if (response.ok) {
            showStatus(`邮箱刷新完成：成功获取 ${data.success_count}/${data.total_count} 个邮箱地址`, 'success');
            // 刷新凭证状态以更新显示
            await refreshCredsStatus();
        } else {
            showStatus(data.message || '邮箱刷新失败', 'error');
        }
    } catch (error) {
        console.error('refreshAllEmails error:', error);
        showStatus(`邮箱刷新网络错误: ${error.message}`, 'error');
    }
}

// =====================================================================
// 环境变量凭证管理相关函数
// =====================================================================

async function checkEnvCredsStatus() {
    const envStatusLoading = document.getElementById('envStatusLoading');
    const envStatusContent = document.getElementById('envStatusContent');

    try {
        envStatusLoading.style.display = 'block';
        envStatusContent.classList.add('hidden');

        const response = await fetch('./auth/env-creds-status', {
            method: 'GET',
            headers: getAuthHeaders()
        });

        const data = await response.json();

        if (response.ok) {
            // 更新环境变量列表
            const envVarsList = document.getElementById('envVarsList');
            if (Object.keys(data.available_env_vars).length > 0) {
                envVarsList.textContent = Object.keys(data.available_env_vars).join(', ');
            } else {
                envVarsList.textContent = '未找到GCLI_CREDS_*环境变量';
            }

            // 更新自动加载状态
            const autoLoadStatus = document.getElementById('autoLoadStatus');
            autoLoadStatus.textContent = data.auto_load_enabled ? '✅ 已启用' : '❌ 未启用';
            autoLoadStatus.style.color = data.auto_load_enabled ? '#28a745' : '#dc3545';

            // 更新已导入文件统计
            const envFilesCount = document.getElementById('envFilesCount');
            envFilesCount.textContent = `${data.existing_env_files_count} 个文件`;

            const envFilesList = document.getElementById('envFilesList');
            if (data.existing_env_files.length > 0) {
                envFilesList.textContent = data.existing_env_files.join(', ');
            } else {
                envFilesList.textContent = '无';
            }

            envStatusContent.classList.remove('hidden');
            showStatus('环境变量状态检查完成', 'success');
        } else {
            showStatus(`获取环境变量状态失败: ${data.detail || data.error || '未知错误'}`, 'error');
        }
    } catch (error) {
        console.error('checkEnvCredsStatus error:', error);
        showStatus(`网络错误: ${error.message}`, 'error');
    } finally {
        envStatusLoading.style.display = 'none';
    }
}

async function loadEnvCredentials() {
    try {
        showStatus('正在从环境变量导入凭证...', 'info');

        const response = await fetch('./auth/load-env-creds', {
            method: 'POST',
            headers: getAuthHeaders()
        });

        const data = await response.json();

        if (response.ok) {
            if (data.loaded_count > 0) {
                showStatus(`✅ 成功导入 ${data.loaded_count}/${data.total_count} 个凭证文件`, 'success');
                // 刷新状态
                setTimeout(() => checkEnvCredsStatus(), 1000);
            } else {
                showStatus(`⚠️ ${data.message}`, 'info');
            }
        } else {
            showStatus(`导入失败: ${data.detail || data.error || '未知错误'}`, 'error');
        }
    } catch (error) {
        console.error('loadEnvCredentials error:', error);
        showStatus(`网络错误: ${error.message}`, 'error');
    }
}

async function clearEnvCredentials() {
    if (!confirm('确定要清除所有从环境变量导入的凭证文件吗？\n这将删除所有文件名以 "env-" 开头的认证文件。')) {
        return;
    }

    try {
        showStatus('正在清除环境变量凭证文件...', 'info');

        const response = await fetch('./auth/env-creds', {
            method: 'DELETE',
            headers: getAuthHeaders()
        });

        const data = await response.json();

        if (response.ok) {
            showStatus(`✅ 成功删除 ${data.deleted_count} 个环境变量凭证文件`, 'success');
            // 刷新状态
            setTimeout(() => checkEnvCredsStatus(), 1000);
        } else {
            showStatus(`清除失败: ${data.detail || data.error || '未知错误'}`, 'error');
        }
    } catch (error) {
        console.error('clearEnvCredentials error:', error);
        showStatus(`网络错误: ${error.message}`, 'error');
    }
}

// =====================================================================
// 配置管理相关函数
// =====================================================================

async function loadConfig() {
    const configLoading = document.getElementById('configLoading');
    const configForm = document.getElementById('configForm');

    try {
        configLoading.style.display = 'block';
        configForm.classList.add('hidden');

        const response = await fetch('./config/get', {
            method: 'GET',
            headers: getAuthHeaders()
        });

        const data = await response.json();

        if (response.ok) {
            currentConfig = data.config;
            envLockedFields = new Set(data.env_locked || []);

            populateConfigForm();
            configForm.classList.remove('hidden');
            showStatus('配置加载成功', 'success');
        } else {
            showStatus(`加载配置失败: ${data.detail || data.error || '未知错误'}`, 'error');
        }
    } catch (error) {
        console.error('loadConfig error:', error);
        showStatus(`网络错误: ${error.message}`, 'error');
    } finally {
        configLoading.style.display = 'none';
    }
}

function populateConfigForm() {
    // 服务器配置
    setConfigField('host', currentConfig.host || '0.0.0.0');
    setConfigField('port', currentConfig.port || 7861);
    setConfigField('configApiPassword', currentConfig.api_password || '');
    setConfigField('configPanelPassword', currentConfig.panel_password || '');
    setConfigField('configPassword', currentConfig.password || 'pwd');

    // 基础配置
    setConfigField('credentialsDir', currentConfig.credentials_dir || '');
    setConfigField('proxy', currentConfig.proxy || '');

    // 端点配置
    setConfigField('codeAssistEndpoint', currentConfig.code_assist_endpoint || '');
    setConfigField('oauthProxyUrl', currentConfig.oauth_proxy_url || '');
    setConfigField('googleapisProxyUrl', currentConfig.googleapis_proxy_url || '');
    setConfigField('resourceManagerApiUrl', currentConfig.resource_manager_api_url || '');
    setConfigField('serviceUsageApiUrl', currentConfig.service_usage_api_url || '');

    // 自动封禁配置
    document.getElementById('autoBanEnabled').checked = Boolean(currentConfig.auto_ban_enabled);
    setConfigField('autoBanErrorCodes', (currentConfig.auto_ban_error_codes || []).join(','));

    // 性能配置
    setConfigField('callsPerRotation', currentConfig.calls_per_rotation || 10);

    // 429重试配置
    document.getElementById('retry429Enabled').checked = Boolean(currentConfig.retry_429_enabled);
    setConfigField('retry429MaxRetries', currentConfig.retry_429_max_retries || 20);
    setConfigField('retry429Interval', currentConfig.retry_429_interval || 0.1);

    // 兼容性配置
    document.getElementById('compatibilityModeEnabled').checked = Boolean(currentConfig.compatibility_mode_enabled);

    // 思维链返回配置
    document.getElementById('returnThoughtsToFrontend').checked = Boolean(currentConfig.return_thoughts_to_frontend !== false);

    // 抗截断配置
    setConfigField('antiTruncationMaxAttempts', currentConfig.anti_truncation_max_attempts || 3);
}

function setConfigField(fieldId, value) {
    const field = document.getElementById(fieldId);
    if (field) {
        field.value = value;

        // 检查是否被环境变量锚定
        const configKey = fieldId.replace(/([A-Z])/g, '_$1').toLowerCase();
        if (envLockedFields.has(configKey)) {
            field.disabled = true;
            field.classList.add('env-locked');
        } else {
            field.disabled = false;
            field.classList.remove('env-locked');
        }
    }
}

async function saveConfig() {
    try {
        // 调试：检查password字段的实际值
        const passwordElement = document.getElementById('configPassword');
        console.log('DEBUG: configPassword元素:', passwordElement);
        console.log('DEBUG: configPassword值:', passwordElement ? passwordElement.value : 'ELEMENT_NOT_FOUND');

        const getElementValue = (id, defaultValue = '') => {
            const element = document.getElementById(id);
            return element ? element.value.trim() : defaultValue;
        };

        const getElementIntValue = (id, defaultValue = 0) => {
            const element = document.getElementById(id);
            return element ? (parseInt(element.value) || defaultValue) : defaultValue;
        };

        const getElementFloatValue = (id, defaultValue = 0.0) => {
            const element = document.getElementById(id);
            return element ?  (parseFloat(element.value) || defaultValue) : defaultValue;
        };

        const getElementChecked = (id, defaultValue = false) => {
            const element = document.getElementById(id);
            return element ? element.checked :  defaultValue;
        };
        const config = {
            host: getElementValue('host', '0.0.0.0'),
            port: getElementIntValue('port', 7861),
            api_password: getElementValue('configApiPassword'),
            panel_password: getElementValue('configPanelPassword'),
            password: getElementValue('configPassword', 'pwd'),
            code_assist_endpoint: getElementValue('codeAssistEndpoint'),
            credentials_dir: getElementValue('credentialsDir'),
            proxy: getElementValue('proxy'),
            // 端点配置
            oauth_proxy_url: getElementValue('oauthProxyUrl'),
            googleapis_proxy_url:  getElementValue('googleapisProxyUrl'),
            resource_manager_api_url: getElementValue('resourceManagerApiUrl'),
            service_usage_api_url:  getElementValue('serviceUsageApiUrl'),
            auto_ban_enabled: getElementChecked('autoBanEnabled'),
            auto_ban_error_codes: getElementValue('autoBanErrorCodes')
                .split(',')
                .map(code => parseInt(code.trim()))
                .filter(code => !isNaN(code)),
            calls_per_rotation: getElementIntValue('callsPerRotation', 10),
            retry_429_enabled: getElementChecked('retry429Enabled'),
            retry_429_max_retries: getElementIntValue('retry429MaxRetries', 20),
            retry_429_interval: getElementFloatValue('retry429Interval', 0.1),
            // 兼容性配置
            compatibility_mode_enabled: getElementChecked('compatibilityModeEnabled'),
            // 思维链返回配置
            return_thoughts_to_frontend: getElementChecked('returnThoughtsToFrontend'),
            // 抗截断配置
            anti_truncation_max_attempts: getElementIntValue('antiTruncationMaxAttempts', 3)
        };

        const response = await fetch('./config/save', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({ config: config })
        });

        const data = await response.json();

        if (response.ok) {
            let message = '配置保存成功';

            // 处理热更新状态信息
            if (data.hot_updated && data.hot_updated.length > 0) {
                message += `，以下配置已立即生效: ${data.hot_updated.join(', ')}`;
            }

            // 处理重启提醒
            if (data.restart_required && data.restart_required.length > 0) {
                message += `\n⚠️ 重启提醒: ${data.restart_notice}`;
                showStatus(message, 'info');
            } else {
                showStatus(message, 'success');
            }

            // 重新加载配置以获取最新状态
            setTimeout(() => loadConfig(), 1000);
        } else {
            showStatus(`保存配置失败: ${data.detail || data.error || '未知错误'}`, 'error');
        }
    } catch (error) {
        console.error('saveConfig error:', error);
        showStatus(`网络错误: ${error.message}`, 'error');
    }
}

// =====================================================================
// 使用统计相关函数
// =====================================================================

async function refreshUsageStats() {
    const usageLoading = document.getElementById('usageLoading');
    const usageList = document.getElementById('usageList');

    try {
        usageLoading.style.display = 'block';
        usageList.innerHTML = '';

        // 获取所有文件的使用统计
        const [statsResponse, aggregatedResponse] = await Promise.all([
            fetch('./usage/stats', {
                method: 'GET',
                headers: getAuthHeaders()
            }),
            fetch('./usage/aggregated', {
                method: 'GET',
                headers: getAuthHeaders()
            })
        ]);

        // 检查认证错误
        if (statsResponse.status === 401 || aggregatedResponse.status === 401) {
            showStatus('认证失败，请重新登录', 'error');
            // 重定向到登录页
            setTimeout(() => {
                location.reload();
            }, 1500);
            return;
        }

        const statsData = await statsResponse.json();
        const aggregatedData = await aggregatedResponse.json();

        if (statsResponse.ok && aggregatedResponse.ok) {
            // API返回格式: { "success": true, "data": {...} }
            usageStatsData = statsData.success ? statsData.data : statsData;

            // 更新概览统计
            const aggData = aggregatedData.success ? aggregatedData.data : aggregatedData;
            document.getElementById('totalApiCalls').textContent = aggData.total_calls_24h || 0;
            document.getElementById('totalFiles').textContent = aggData.total_files || 0;
            document.getElementById('avgCallsPerFile').textContent = (aggData.avg_calls_per_file || 0).toFixed(1);

            // 渲染使用统计列表
            renderUsageList();

            showStatus(`已加载 ${aggData.total_files || Object.keys(usageStatsData).length} 个文件的使用统计`, 'success');
        } else {
            const errorMsg = statsData.detail || aggregatedData.detail || '加载使用统计失败';
            showStatus(`错误: ${errorMsg}`, 'error');
        }
    } catch (error) {
        console.error('refreshUsageStats error:', error);
        showStatus(`网络错误: ${error.message}`, 'error');
    } finally {
        usageLoading.style.display = 'none';
    }
}

function renderUsageList() {
    const usageList = document.getElementById('usageList');
    usageList.innerHTML = '';

    if (Object.keys(usageStatsData).length === 0) {
        usageList.innerHTML = '<p style="text-align: center; color: #666;">暂无使用统计数据</p>';
        return;
    }

    for (const [filename, stats] of Object.entries(usageStatsData)) {
        const card = createUsageCard(filename, stats);
        usageList.appendChild(card);
    }
}

function createUsageCard(filename, stats) {
    const div = document.createElement('div');
    div.className = 'usage-card';

    const calls24h = stats.calls_24h || 0;

    div.innerHTML = `
        <div class="usage-header">
            <div class="usage-filename">${filename}</div>
        </div>

        <div class="usage-info">
            <div class="usage-info-item" style="grid-column: 1 / -1;">
                <span class="usage-info-label">24小时内调用次数</span>
                <span class="usage-info-value" style="font-size: 24px; font-weight: bold; color: #007bff;">${calls24h}</span>
            </div>
        </div>

        <div class="usage-actions">
            <button class="usage-btn reset" onclick="resetSingleUsageStats('${filename}')">重置统计</button>
        </div>
    `;

    return div;
}

async function resetSingleUsageStats(filename) {
    if (!confirm(`确定要重置 ${filename} 的使用统计吗？`)) {
        return;
    }

    try {
        const response = await fetch('./usage/reset', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({ filename: filename })
        });

        const data = await response.json();

        if (response.ok && data.success) {
            showStatus(data.message, 'success');
            await refreshUsageStats();
        } else {
            showStatus(`重置失败: ${data.message || data.detail || data.error || '未知错误'}`, 'error');
        }
    } catch (error) {
        console.error('resetSingleUsageStats error:', error);
        showStatus(`网络错误: ${error.message}`, 'error');
    }
}

async function resetAllUsageStats() {
    if (!confirm('确定要重置所有文件的使用统计吗？此操作不可恢复！')) {
        return;
    }

    try {
        const response = await fetch('./usage/reset', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({})  // 不提供filename表示重置所有
        });

        const data = await response.json();

        if (response.ok && data.success) {
            showStatus(data.message, 'success');
            await refreshUsageStats();
        } else {
            showStatus(`重置失败: ${data.message || data.detail || data.error || '未知错误'}`, 'error');
        }
    } catch (error) {
        console.error('resetAllUsageStats error:', error);
        showStatus(`网络错误: ${error.message}`, 'error');
    }
}

// =====================================================================
// 端点配置快速切换函数
// =====================================================================

// 镜像网址配置
const mirrorUrls = {
    codeAssistEndpoint: 'https://gcli-api.sukaka.top/cloudcode-pa',
    oauthProxyUrl: 'https://gcli-api.sukaka.top/oauth2',
    googleapisProxyUrl: 'https://gcli-api.sukaka.top/googleapis',
    resourceManagerApiUrl: 'https://gcli-api.sukaka.top/cloudresourcemanager',
    serviceUsageApiUrl: 'https://gcli-api.sukaka.top/serviceusage'
};

// 官方端点配置
const officialUrls = {
    codeAssistEndpoint: 'https://cloudcode-pa.googleapis.com',
    oauthProxyUrl: 'https://oauth2.googleapis.com',
    googleapisProxyUrl: 'https://www.googleapis.com',
    resourceManagerApiUrl: 'https://cloudresourcemanager.googleapis.com',
    serviceUsageApiUrl: 'https://serviceusage.googleapis.com'
};

function useMirrorUrls() {
    if (confirm('确定要将所有端点配置为镜像网址吗？\n\n镜像网址：\n• Code Assist: https://gcli-api.sukaka.top/cloudcode-pa\n• OAuth: https://gcli-api.sukaka.top/oauth2\n• Google APIs: https://gcli-api.sukaka.top/googleapis\n• Resource Manager: https://gcli-api.sukaka.top/cloudresourcemanager\n• Service Usage: https://gcli-api.sukaka.top/serviceusage')) {

        // 设置所有端点为镜像网址
        for (const [fieldId, url] of Object.entries(mirrorUrls)) {
            const field = document.getElementById(fieldId);
            if (field && !field.disabled) {
                field.value = url;
            }
        }

        showStatus('✅ 已切换到镜像网址配置，记得点击"保存配置"按钮保存设置', 'success');
    }
}

function restoreOfficialUrls() {
    if (confirm('确定要将所有端点配置为官方地址吗？\n\n官方端点：\n• Code Assist: https://cloudcode-pa.googleapis.com\n• OAuth: https://oauth2.googleapis.com\n• Google APIs: https://www.googleapis.com\n• Resource Manager: https://cloudresourcemanager.googleapis.com\n• Service Usage: https://serviceusage.googleapis.com')) {

        // 设置所有端点为官方地址
        for (const [fieldId, url] of Object.entries(officialUrls)) {
            const field = document.getElementById(fieldId);
            if (field && !field.disabled) {
                field.value = url;
            }
        }

        showStatus('✅ 已切换到官方端点配置，记得点击"保存配置"按钮保存设置', 'success');
    }
}

// =====================================================================
// 冷却倒计时自动更新
// =====================================================================

function startCooldownTimer() {
    // 清除旧的定时器
    if (cooldownTimerInterval) {
        clearInterval(cooldownTimerInterval);
    }

    // 每秒更新一次冷却状态
    cooldownTimerInterval = setInterval(() => {
        updateCooldownDisplays();
    }, 1000);
}

function stopCooldownTimer() {
    if (cooldownTimerInterval) {
        clearInterval(cooldownTimerInterval);
        cooldownTimerInterval = null;
    }
}

function updateCooldownDisplays() {
    // 遍历所有凭证，更新冷却显示
    for (const [fullPath, credInfo] of Object.entries(credsData)) {
        if (credInfo.cooldown_status === 'cooling' && credInfo.cooldown_until) {
            const currentTime = Date.now() / 1000; // 当前时间（秒）
            const remainingSeconds = Math.max(0, Math.floor(credInfo.cooldown_until - currentTime));

            // 更新内存中的剩余时间
            credInfo.cooldown_remaining_seconds = remainingSeconds;

            // 如果冷却期已过，标记为ready并刷新列表
            if (remainingSeconds <= 0) {
                credInfo.cooldown_status = 'ready';
                credInfo.cooldown_until = null;
                credInfo.cooldown_remaining_seconds = 0;

                // 重新渲染当前页（避免频繁刷新整个列表）
                renderCredsList();
                return; // 有状态变化，立即重新渲染
            }
        }
    }

    // 更新页面上的冷却显示（只更新文字，不重新渲染整个卡片）
    document.querySelectorAll('.cooldown-badge').forEach(badge => {
        const filenameMatch = badge.closest('.cred-card')?.querySelector('.cred-filename')?.textContent;
        if (!filenameMatch) return;

        // 找到对应的凭证数据
        for (const [fullPath, credInfo] of Object.entries(credsData)) {
            if (credInfo.filename === filenameMatch && credInfo.cooldown_status === 'cooling') {
                const remainingSeconds = credInfo.cooldown_remaining_seconds || 0;
                if (remainingSeconds > 0) {
                    const hours = Math.floor(remainingSeconds / 3600);
                    const minutes = Math.floor((remainingSeconds % 3600) / 60);
                    const seconds = remainingSeconds % 60;

                    let timeDisplay = '';
                    if (hours > 0) {
                        timeDisplay = `${hours}h ${minutes}m ${seconds}s`;
                    } else if (minutes > 0) {
                        timeDisplay = `${minutes}m ${seconds}s`;
                    } else {
                        timeDisplay = `${seconds}s`;
                    }

                    // 只更新时间文本
                    badge.innerHTML = `🕐 冷却中: ${timeDisplay}`;
                }
                break;
            }
        }
    });
}

// =====================================================================
// 页面初始化
// =====================================================================

window.onload = async function () {
    console.log('Page loaded');
    console.log('Login section exists:', !!document.getElementById('loginSection'));
    console.log('Main section exists:', !!document.getElementById('mainSection'));
    console.log('Status section exists:', !!document.getElementById('statusSection'));

    // 尝试自动登录
    const autoLoginSuccess = await autoLogin();

    if (!autoLoginSuccess) {
        // 自动登录失败，显示登录提示
        showStatus('请输入密码登录', 'info');
    }

    // 添加勾选框事件监听器
    const checkbox = document.getElementById('getAllProjectsCreds');
    if (checkbox) {
        checkbox.addEventListener('change', handleGetAllProjectsChange);
    }

    // 启动冷却倒计时自动更新（每秒更新一次）
    startCooldownTimer();
};

// 拖拽功能 - 初始化
document.addEventListener('DOMContentLoaded', function() {
    const uploadArea = document.getElementById('uploadArea');

    if (uploadArea) {
        uploadArea.addEventListener('dragover', function (event) {
            event.preventDefault();
            uploadArea.classList.add('dragover');
        });

        uploadArea.addEventListener('dragleave', function (event) {
            event.preventDefault();
            uploadArea.classList.remove('dragover');
        });

        uploadArea.addEventListener('drop', function (event) {
            event.preventDefault();
            uploadArea.classList.remove('dragover');

            const files = Array.from(event.dataTransfer.files);
            addFiles(files);
        });
    }
});
