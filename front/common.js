// =====================================================================
// GCLI2API $id_701JavaScript$id_702
// =====================================================================

// =====================================================================
// $id_703
// =====================================================================
const AppState = {
    // $id_704
    authToken: '',
    authInProgress: false,
    currentProjectId: '',

    // Antigravity$id_251
    antigravityAuthState: null,
    antigravityAuthInProgress: false,

    // $id_705
    creds: createCredsManager('normal'),
    antigravityCreds: createCredsManager('antigravity'),

    // $id_706
    uploadFiles: createUploadManager('normal'),
    antigravityUploadFiles: createUploadManager('antigravity'),

    // $id_707
    currentConfig: {},
    envLockedFields: new Set(),

    // $id_708
    logWebSocket: null,
    allLogs: [],
    filteredLogs: [],
    currentLogFilter: 'all',

    // $id_709
    usageStatsData: {},

    // $id_710
    cooldownTimerInterval: null
};

// =====================================================================
// $id_711
// =====================================================================
function createCredsManager(type) {
    const modeParam = type === 'antigravity' ? 'mode=antigravity' : 'mode=geminicli';

    return {
        type: type,
        data: {},
        filteredData: {},
        currentPage: 1,
        pageSize: 20,
        selectedFiles: new Set(),
        totalCount: 0,
        currentStatusFilter: 'all',
        currentErrorCodeFilter: 'all',
        currentCooldownFilter: 'all',
        statsData: { total: 0, normal: 0, disabled: 0 },

        // API$id_58
        getEndpoint: (action) => {
            const endpoints = {
                status: `./creds/status`,
                action: `./creds/action`,
                batchAction: `./creds/batch-action`,
                download: `./creds/download`,
                downloadAll: `./creds/download-all`,
                detail: `./creds/detail`,
                fetchEmail: `./creds/fetch-email`,
                refreshAllEmails: `./creds/refresh-all-emails`,
                deduplicate: `./creds/deduplicate-by-email`,
                verifyProject: `./creds/verify-project`,
                quota: `./creds/quota`
            };
            return endpoints[action] || '';
        },

        // $id_712mode$id_226
        getModeParam: () => modeParam,

        // DOM$id_713ID$id_365
        getElementId: (suffix) => {
            // $id_714ID$id_715,$id_716 credsLoading
            // Antigravity$id_61ID$id_150 antigravity + $id_717,$id_716 antigravityCredsLoading
            if (type === 'antigravity') {
                return 'antigravity' + suffix.charAt(0).toUpperCase() + suffix.slice(1);
            }
            return suffix.charAt(0).toLowerCase() + suffix.slice(1);
        },

        // $id_718
        async refresh() {
            const loading = document.getElementById(this.getElementId('CredsLoading'));
            const list = document.getElementById(this.getElementId('CredsList'));

            try {
                loading.style.display = 'block';
                list.innerHTML = '';

                const offset = (this.currentPage - 1) * this.pageSize;
                const errorCodeFilter = this.currentErrorCodeFilter || 'all';
                const cooldownFilter = this.currentCooldownFilter || 'all';
                const response = await fetch(
                    `${this.getEndpoint('status')}?offset=${offset}&limit=${this.pageSize}&status_filter=${this.currentStatusFilter}&error_code_filter=${errorCodeFilter}&cooldown_filter=${cooldownFilter}&${this.getModeParam()}`,
                    { headers: getAuthHeaders() }
                );

                const data = await response.json();

                if (response.ok) {
                    this.data = {};
                    data.items.forEach(item => {
                        this.data[item.filename] = {
                            filename: item.filename,
                            status: {
                                disabled: item.disabled,
                                error_codes: item.error_codes || [],
                                last_success: item.last_success,
                            },
                            user_email: item.user_email,
                            model_cooldowns: item.model_cooldowns || {}
                        };
                    });

                    this.totalCount = data.total;
                    // $id_719
                    if (data.stats) {
                        this.statsData = data.stats;
                    } else {
                        // $id_720
                        this.calculateStats();
                    }
                    this.updateStatsDisplay();
                    this.filteredData = this.data;
                    this.renderList();
                    this.updatePagination();

                    let msg = `$id_722 ${data.total} $id_723${type === 'antigravity' ? 'Antigravity' : ''}$id_721`;
                    if (this.currentStatusFilter !== 'all') {
                        msg += ` ($id_726: ${this.currentStatusFilter === 'enabled' ? '$id_724' : '$id_725'})`;
                    }
                    showStatus(msg, 'success');
                } else {
                    showStatus(`$id_728: ${data.detail || data.error || '$id_727'}`, 'error');
                }
            } catch (error) {
                showStatus(`$id_729: ${error.message}`, 'error');
            } finally {
                loading.style.display = 'none';
            }
        },

        // $id_730
        calculateStats() {
            this.statsData = { total: this.totalCount, normal: 0, disabled: 0 };
            Object.values(this.data).forEach(credInfo => {
                if (credInfo.status.disabled) {
                    this.statsData.disabled++;
                } else {
                    this.statsData.normal++;
                }
            });
        },

        // $id_731
        updateStatsDisplay() {
            document.getElementById(this.getElementId('StatTotal')).textContent = this.statsData.total;
            document.getElementById(this.getElementId('StatNormal')).textContent = this.statsData.normal;
            document.getElementById(this.getElementId('StatDisabled')).textContent = this.statsData.disabled;
        },

        // $id_732
        renderList() {
            const list = document.getElementById(this.getElementId('CredsList'));
            list.innerHTML = '';

            const entries = Object.entries(this.filteredData);

            if (entries.length === 0) {
                const msg = this.totalCount === 0 ? '$id_734' : '$id_733';
                list.innerHTML = `<p style="text-align: center; color: #666;">${msg}</p>`;
                document.getElementById(this.getElementId('PaginationContainer')).style.display = 'none';
                return;
            }

            entries.forEach(([, credInfo]) => {
                list.appendChild(createCredCard(credInfo, this));
            });

            document.getElementById(this.getElementId('PaginationContainer')).style.display =
                this.getTotalPages() > 1 ? 'flex' : 'none';
            this.updateBatchControls();
        },

        // $id_735
        getTotalPages() {
            return Math.ceil(this.totalCount / this.pageSize);
        },

        // $id_736
        updatePagination() {
            const totalPages = this.getTotalPages();
            const startItem = (this.currentPage - 1) * this.pageSize + 1;
            const endItem = Math.min(this.currentPage * this.pageSize, this.totalCount);

            document.getElementById(this.getElementId('PaginationInfo')).textContent =
                `$id_742 ${this.currentPage} $id_737 ${totalPages} $id_740 ($id_739 ${startItem}-${endItem}$id_738 ${this.totalCount} $id_741)`;

            document.getElementById(this.getElementId('PrevPageBtn')).disabled = this.currentPage <= 1;
            document.getElementById(this.getElementId('NextPageBtn')).disabled = this.currentPage >= totalPages;
        },

        // $id_743
        changePage(direction) {
            const newPage = this.currentPage + direction;
            if (newPage >= 1 && newPage <= this.getTotalPages()) {
                this.currentPage = newPage;
                this.refresh();
            }
        },

        // $id_744
        changePageSize() {
            this.pageSize = parseInt(document.getElementById(this.getElementId('PageSizeSelect')).value);
            this.currentPage = 1;
            this.refresh();
        },

        // $id_745
        applyStatusFilter() {
            this.currentStatusFilter = document.getElementById(this.getElementId('StatusFilter')).value;
            const errorCodeFilterEl = document.getElementById(this.getElementId('ErrorCodeFilter'));
            const cooldownFilterEl = document.getElementById(this.getElementId('CooldownFilter'));
            this.currentErrorCodeFilter = errorCodeFilterEl ? errorCodeFilterEl.value : 'all';
            this.currentCooldownFilter = cooldownFilterEl ? cooldownFilterEl.value : 'all';
            this.currentPage = 1;
            this.refresh();
        },

        // $id_746
        updateBatchControls() {
            const selectedCount = this.selectedFiles.size;
            document.getElementById(this.getElementId('SelectedCount')).textContent = `$id_747 ${selectedCount} $id_741`;

            const batchBtns = ['Enable', 'Disable', 'Delete', 'Verify'].map(action =>
                document.getElementById(this.getElementId(`Batch${action}Btn`))
            );
            batchBtns.forEach(btn => btn && (btn.disabled = selectedCount === 0));

            const selectAllCheckbox = document.getElementById(this.getElementId('SelectAllCheckbox'));
            if (!selectAllCheckbox) return;

            const checkboxes = document.querySelectorAll(`.${this.getElementId('file-checkbox')}`);
            const currentPageSelectedCount = Array.from(checkboxes)
                .filter(cb => this.selectedFiles.has(cb.getAttribute('data-filename'))).length;

            if (currentPageSelectedCount === 0) {
                selectAllCheckbox.indeterminate = false;
                selectAllCheckbox.checked = false;
            } else if (currentPageSelectedCount === checkboxes.length) {
                selectAllCheckbox.indeterminate = false;
                selectAllCheckbox.checked = true;
            } else {
                selectAllCheckbox.indeterminate = true;
            }

            checkboxes.forEach(cb => {
                cb.checked = this.selectedFiles.has(cb.getAttribute('data-filename'));
            });
        },

        // $id_748
        async action(filename, action) {
            try {
                const response = await fetch(`${this.getEndpoint('action')}?${this.getModeParam()}`, {
                    method: 'POST',
                    headers: getAuthHeaders(),
                    body: JSON.stringify({ filename, action })
                });

                const data = await response.json();

                if (response.ok) {
                    showStatus(data.message || `$id_749: ${action}`, 'success');
                    await this.refresh();
                } else {
                    showStatus(`$id_750: ${data.detail || data.error || '$id_727'}`, 'error');
                }
            } catch (error) {
                showStatus(`$id_729: ${error.message}`, 'error');
            }
        },

        // $id_751
        async batchAction(action) {
            const selectedFiles = Array.from(this.selectedFiles);

            if (selectedFiles.length === 0) {
                showStatus('$id_752', 'error');
                return;
            }

            const actionNames = { enable: '$id_126', disable: '$id_300', delete: '$id_753' };
            const confirmMsg = action === 'delete'
                ? `$id_755 ${selectedFiles.length} $id_756\n$id_754`
                : `$id_757${actionNames[action]}$id_758 ${selectedFiles.length} $id_756`;

            if (!confirm(confirmMsg)) return;

            try {
                showStatus(`$id_759${actionNames[action]}$id_760...`, 'info');

                const response = await fetch(`${this.getEndpoint('batchAction')}?${this.getModeParam()}`, {
                    method: 'POST',
                    headers: getAuthHeaders(),
                    body: JSON.stringify({ action, filenames: selectedFiles })
                });

                const data = await response.json();

                if (response.ok) {
                    const successCount = data.success_count || data.succeeded;
                    showStatus(`$id_761 ${successCount}/${selectedFiles.length} $id_762`, 'success');
                    this.selectedFiles.clear();
                    this.updateBatchControls();
                    await this.refresh();
                } else {
                    showStatus(`$id_763: ${data.detail || data.error || '$id_727'}`, 'error');
                }
            } catch (error) {
                showStatus(`$id_764: ${error.message}`, 'error');
            }
        }
    };
}

// =====================================================================
// $id_765
// =====================================================================
function createUploadManager(type) {
    const modeParam = type === 'antigravity' ? 'mode=antigravity' : 'mode=geminicli';
    const endpoint = `./creds/upload?${modeParam}`;

    return {
        type: type,
        selectedFiles: [],

        getElementId: (suffix) => {
            // $id_766ID$id_715,$id_716 fileList
            // Antigravity$id_61ID$id_150 antigravity + $id_717,$id_716 antigravityFileList
            if (type === 'antigravity') {
                return 'antigravity' + suffix.charAt(0).toUpperCase() + suffix.slice(1);
            }
            return suffix.charAt(0).toLowerCase() + suffix.slice(1);
        },

        handleFileSelect(event) {
            this.addFiles(Array.from(event.target.files));
        },

        addFiles(files) {
            files.forEach(file => {
                const isValid = file.type === 'application/json' || file.name.endsWith('.json') ||
                    file.type === 'application/zip' || file.name.endsWith('.zip');

                if (isValid) {
                    if (!this.selectedFiles.find(f => f.name === file.name && f.size === file.size)) {
                        this.selectedFiles.push(file);
                    }
                } else {
                    showStatus(`$id_112 ${file.name} $id_767JSON$id_15ZIP$id_112`, 'error');
                }
            });
            this.updateFileList();
        },

        updateFileList() {
            const list = document.getElementById(this.getElementId('FileList'));
            const section = document.getElementById(this.getElementId('FileListSection'));

            if (!list || !section) {
                console.warn('File list elements not found:', this.getElementId('FileList'));
                return;
            }

            if (this.selectedFiles.length === 0) {
                section.classList.add('hidden');
                return;
            }

            section.classList.remove('hidden');
            list.innerHTML = '';

            this.selectedFiles.forEach((file, index) => {
                const isZip = file.name.endsWith('.zip');
                const fileIcon = isZip ? '📦' : '📄';
                const fileType = isZip ? ' (ZIP$id_768)' : ' (JSON$id_112)';

                const fileItem = document.createElement('div');
                fileItem.className = 'file-item';
                fileItem.innerHTML = `
                    <div>
                        <span class="file-name">${fileIcon} ${file.name}</span>
                        <span class="file-size">(${formatFileSize(file.size)}${fileType})</span>
                    </div>
                    <button class="remove-btn" onclick="${type === 'antigravity' ? 'removeAntigravityFile' : 'removeFile'}(${index})">$id_753</button>
                `;
                list.appendChild(fileItem);
            });
        },

        removeFile(index) {
            this.selectedFiles.splice(index, 1);
            this.updateFileList();
        },

        clearFiles() {
            this.selectedFiles = [];
            this.updateFileList();
        },

        async upload() {
            if (this.selectedFiles.length === 0) {
                showStatus('$id_769', 'error');
                return;
            }

            const progressSection = document.getElementById(this.getElementId('UploadProgressSection'));
            const progressFill = document.getElementById(this.getElementId('ProgressFill'));
            const progressText = document.getElementById(this.getElementId('ProgressText'));

            progressSection.classList.remove('hidden');

            const formData = new FormData();
            this.selectedFiles.forEach(file => formData.append('files', file));

            if (this.selectedFiles.some(f => f.name.endsWith('.zip'))) {
                showStatus('$id_770ZIP$id_112...', 'info');
            }

            try {
                const xhr = new XMLHttpRequest();
                xhr.timeout = 300000; // 5$id_771

                xhr.upload.onprogress = (event) => {
                    if (event.lengthComputable) {
                        const percent = (event.loaded / event.total) * 100;
                        progressFill.style.width = percent + '%';
                        progressText.textContent = Math.round(percent) + '%';
                    }
                };

                xhr.onload = () => {
                    if (xhr.status === 200) {
                        try {
                            const data = JSON.parse(xhr.responseText);
                            showStatus(`$id_772 ${data.uploaded_count} $id_723${type === 'antigravity' ? 'Antigravity' : ''}$id_112`, 'success');
                            this.clearFiles();
                            progressSection.classList.add('hidden');
                        } catch (e) {
                            showStatus('$id_774: $id_773', 'error');
                        }
                    } else {
                        try {
                            const error = JSON.parse(xhr.responseText);
                            showStatus(`$id_774: ${error.detail || error.error || '$id_727'}`, 'error');
                        } catch (e) {
                            showStatus(`$id_774: HTTP ${xhr.status}`, 'error');
                        }
                    }
                };

                xhr.onerror = () => {
                    showStatus(`$id_776 - $id_777(${this.selectedFiles.length}$id_723)$id_775`, 'error');
                    progressSection.classList.add('hidden');
                };

                xhr.ontimeout = () => {
                    showStatus('$id_779 - $id_778', 'error');
                    progressSection.classList.add('hidden');
                };

                xhr.open('POST', endpoint);
                xhr.setRequestHeader('Authorization', `Bearer ${AppState.authToken}`);
                xhr.send(formData);
            } catch (error) {
                showStatus(`$id_774: ${error.message}`, 'error');
            }
        }
    };
}

// =====================================================================
// $id_780
// =====================================================================
function showStatus(message, type = 'info') {
    const statusSection = document.getElementById('statusSection');
    if (statusSection) {
        // $id_781
        if (window._statusTimeout) {
            clearTimeout(window._statusTimeout);
        }

        // $id_782 toast
        statusSection.innerHTML = `<div class="status ${type}">${message}</div>`;
        const statusDiv = statusSection.querySelector('.status');

        // $id_783
        statusDiv.offsetHeight;
        statusDiv.classList.add('show');

        // 3$id_784
        window._statusTimeout = setTimeout(() => {
            statusDiv.classList.add('fade-out');
            setTimeout(() => {
                statusSection.innerHTML = '';
            }, 300); // $id_785
        }, 3000);
    } else {
        alert(message);
    }
}

function getAuthHeaders() {
    return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${AppState.authToken}`
    };
}

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + ' KB';
    return Math.round(bytes / (1024 * 1024)) + ' MB';
}

function formatCooldownTime(remainingSeconds) {
    const hours = Math.floor(remainingSeconds / 3600);
    const minutes = Math.floor((remainingSeconds % 3600) / 60);
    const seconds = remainingSeconds % 60;

    if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
    if (minutes > 0) return `${minutes}m ${seconds}s`;
    return `${seconds}s`;
}

// =====================================================================
// $id_786
// =====================================================================
function createCredCard(credInfo, manager) {
    const div = document.createElement('div');
    const { status, filename } = credInfo;
    const managerType = manager.type;

    // $id_787
    div.className = status.disabled ? 'cred-card disabled' : 'cred-card';

    // $id_788
    let statusBadges = '';
    statusBadges += status.disabled
        ? '<span class="status-badge disabled">$id_789</span>'
        : '<span class="status-badge enabled">$id_790</span>';

    if (status.error_codes && status.error_codes.length > 0) {
        statusBadges += `<span class="error-codes">$id_791: ${status.error_codes.join(', ')}</span>`;
        const autoBan = status.error_codes.filter(c => c === 400 || c === 403);
        if (autoBan.length > 0 && status.disabled) {
            statusBadges += '<span class="status-badge" style="background-color: #e74c3c; color: white;">AUTO_BAN</span>';
        }
    } else {
        statusBadges += '<span class="status-badge" style="background-color: #28a745; color: white;">$id_792</span>';
    }

    // $id_793
    if (credInfo.model_cooldowns && Object.keys(credInfo.model_cooldowns).length > 0) {
        const currentTime = Date.now() / 1000;
        const activeCooldowns = Object.entries(credInfo.model_cooldowns)
            .filter(([, until]) => until > currentTime)
            .map(([model, until]) => {
                const remaining = Math.max(0, Math.floor(until - currentTime));
                const shortModel = model.replace('gemini-', '').replace('-exp', '')
                    .replace('2.0-', '2-').replace('1.5-', '1.5-');
                return {
                    model: shortModel,
                    time: formatCooldownTime(remaining).replace(/s$/, '').replace(/ /g, ''),
                    fullModel: model
                };
            });

        if (activeCooldowns.length > 0) {
            activeCooldowns.slice(0, 2).forEach(item => {
                statusBadges += `<span class="cooldown-badge" style="background-color: #17a2b8;" title="$id_794: ${item.fullModel}">🔧 ${item.model}: ${item.time}</span>`;
            });
            if (activeCooldowns.length > 2) {
                const remaining = activeCooldowns.length - 2;
                const remainingModels = activeCooldowns.slice(2).map(i => `${i.fullModel}: ${i.time}`).join('\n');
                statusBadges += `<span class="cooldown-badge" style="background-color: #17a2b8;" title="$id_795:\n${remainingModels}">+${remaining}</span>`;
            }
        }
    }

    // $id_796ID
    const pathId = (managerType === 'antigravity' ? 'ag_' : '') + btoa(encodeURIComponent(filename)).replace(/[+/=]/g, '_');

    // $id_797
    const actionButtons = `
        ${status.disabled
            ? `<button class="cred-btn enable" data-filename="${filename}" data-action="enable">$id_126</button>`
            : `<button class="cred-btn disable" data-filename="${filename}" data-action="disable">$id_300</button>`
        }
        <button class="cred-btn view" onclick="toggle${managerType === 'antigravity' ? 'Antigravity' : ''}CredDetails('${pathId}')">$id_798</button>
        <button class="cred-btn download" onclick="download${managerType === 'antigravity' ? 'Antigravity' : ''}Cred('${filename}')">$id_799</button>
        <button class="cred-btn email" onclick="fetch${managerType === 'antigravity' ? 'Antigravity' : ''}UserEmail('${filename}')">$id_800</button>
        ${managerType === 'antigravity' ? `<button class="cred-btn" style="background-color: #17a2b8;" onclick="toggleAntigravityQuotaDetails('${pathId}')" title="$id_801">$id_802</button>` : ''}
        <button class="cred-btn" style="background-color: #ff9800;" onclick="verify${managerType === 'antigravity' ? 'Antigravity' : ''}ProjectId('${filename}')" title="$id_804Project ID$id_803403$id_806">$id_805</button>
        <button class="cred-btn delete" data-filename="${filename}" data-action="delete">$id_753</button>
    `;

    // $id_807
    const emailInfo = credInfo.user_email
        ? `<div class="cred-email" style="font-size: 12px; color: #666; margin-top: 2px;">${credInfo.user_email}</div>`
        : '<div class="cred-email" style="font-size: 12px; color: #999; margin-top: 2px; font-style: italic;">$id_808</div>';

    const checkboxClass = manager.getElementId('file-checkbox');

    div.innerHTML = `
        <div class="cred-header">
            <div style="display: flex; align-items: center; gap: 10px;">
                <input type="checkbox" class="${checkboxClass}" data-filename="${filename}" onchange="toggle${managerType === 'antigravity' ? 'Antigravity' : ''}FileSelection('${filename}')">
                <div>
                    <div class="cred-filename">${filename}</div>
                    ${emailInfo}
                </div>
            </div>
            <div class="cred-status">${statusBadges}</div>
        </div>
        <div class="cred-actions">${actionButtons}</div>
        <div class="cred-details" id="details-${pathId}">
            <div class="cred-content" data-filename="${filename}" data-loaded="false">$id_810"$id_798"$id_809...</div>
        </div>
        ${managerType === 'antigravity' ? `
        <div class="cred-quota-details" id="quota-${pathId}" style="display: none;">
            <div class="cred-quota-content" data-filename="${filename}" data-loaded="false">
                $id_810"$id_802"$id_811...
            </div>
        </div>
        ` : ''}
    `;

    // $id_812
    div.querySelectorAll('[data-filename][data-action]').forEach(button => {
        button.addEventListener('click', function () {
            const fn = this.getAttribute('data-filename');
            const action = this.getAttribute('data-action');
            if (action === 'delete') {
                if (confirm(`$id_814${managerType === 'antigravity' ? ' Antigravity ' : ''}$id_813\n${fn}`)) {
                    manager.action(fn, action);
                }
            } else {
                manager.action(fn, action);
            }
        });
    });

    return div;
}

// =====================================================================
// $id_815
// =====================================================================
async function toggleCredDetails(pathId) {
    await toggleCredDetailsCommon(pathId, AppState.creds);
}

async function toggleAntigravityCredDetails(pathId) {
    await toggleCredDetailsCommon(pathId, AppState.antigravityCreds);
}

async function toggleCredDetailsCommon(pathId, manager) {
    const details = document.getElementById('details-' + pathId);
    if (!details) return;

    const isShowing = details.classList.toggle('show');

    if (isShowing) {
        const contentDiv = details.querySelector('.cred-content');
        const filename = contentDiv.getAttribute('data-filename');
        const loaded = contentDiv.getAttribute('data-loaded');

        if (loaded === 'false' && filename) {
            contentDiv.textContent = '$id_816...';

            try {
                const modeParam = manager.type === 'antigravity' ? 'mode=antigravity' : 'mode=geminicli';
                const endpoint = `./creds/detail/${encodeURIComponent(filename)}?${modeParam}`;

                const response = await fetch(endpoint, { headers: getAuthHeaders() });

                const data = await response.json();
                if (response.ok && data.content) {
                    contentDiv.textContent = JSON.stringify(data.content, null, 2);
                    contentDiv.setAttribute('data-loaded', 'true');
                } else {
                    contentDiv.textContent = '$id_817: ' + (data.error || data.detail || '$id_727');
                }
            } catch (error) {
                contentDiv.textContent = '$id_818: ' + error.message;
            }
        }
    }
}

// =====================================================================
// $id_819
// =====================================================================
async function login() {
    const password = document.getElementById('loginPassword').value;

    if (!password) {
        showStatus('$id_820', 'error');
        return;
    }

    try {
        const response = await fetch('./auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password })
        });

        const data = await response.json();

        if (response.ok) {
            AppState.authToken = data.token;
            localStorage.setItem('gcli2api_auth_token', AppState.authToken);
            document.getElementById('loginSection').classList.add('hidden');
            document.getElementById('mainSection').classList.remove('hidden');
            showStatus('$id_821', 'success');
            // $id_822
            requestAnimationFrame(() => initTabSlider());
        } else {
            showStatus(`$id_823: ${data.detail || data.error || '$id_727'}`, 'error');
        }
    } catch (error) {
        showStatus(`$id_729: ${error.message}`, 'error');
    }
}

async function autoLogin() {
    const savedToken = localStorage.getItem('gcli2api_auth_token');
    if (!savedToken) return false;

    AppState.authToken = savedToken;

    try {
        const response = await fetch('./config/get', {
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${AppState.authToken}`
            }
        });

        if (response.ok) {
            document.getElementById('loginSection').classList.add('hidden');
            document.getElementById('mainSection').classList.remove('hidden');
            showStatus('$id_824', 'success');
            // $id_822
            requestAnimationFrame(() => initTabSlider());
            return true;
        } else if (response.status === 401) {
            localStorage.removeItem('gcli2api_auth_token');
            AppState.authToken = '';
            return false;
        }
        return false;
    } catch (error) {
        return false;
    }
}

function logout() {
    localStorage.removeItem('gcli2api_auth_token');
    AppState.authToken = '';
    document.getElementById('loginSection').classList.remove('hidden');
    document.getElementById('mainSection').classList.add('hidden');
    showStatus('$id_825', 'info');
    const passwordInput = document.getElementById('loginPassword');
    if (passwordInput) passwordInput.value = '';
}

function handlePasswordEnter(event) {
    if (event.key === 'Enter') login();
}

// =====================================================================
// $id_826
// =====================================================================

// $id_827
function updateTabSlider(targetTab, animate = true) {
    const slider = document.querySelector('.tab-slider');
    const tabs = document.querySelector('.tabs');
    if (!slider || !tabs || !targetTab) return;

    // $id_828
    const tabLeft = targetTab.offsetLeft;
    const tabWidth = targetTab.offsetWidth;
    const tabsWidth = tabs.scrollWidth;

    // $id_463 left $id_15 right $id_829
    const rightValue = tabsWidth - tabLeft - tabWidth;

    if (animate) {
        slider.style.left = `${tabLeft}px`;
        slider.style.right = `${rightValue}px`;
    } else {
        // $id_830
        slider.style.transition = 'none';
        slider.style.left = `${tabLeft}px`;
        slider.style.right = `${rightValue}px`;
        // $id_831
        slider.offsetHeight;
        slider.style.transition = '';
    }
}

// $id_832
function initTabSlider() {
    const activeTab = document.querySelector('.tab.active');
    if (activeTab) {
        updateTabSlider(activeTab, false);
    }
}

// $id_833
document.addEventListener('DOMContentLoaded', initTabSlider);
window.addEventListener('resize', () => {
    const activeTab = document.querySelector('.tab.active');
    if (activeTab) updateTabSlider(activeTab, false);
});

function switchTab(tabName) {
    // $id_834
    const currentContent = document.querySelector('.tab-content.active');
    const targetContent = document.getElementById(tabName + 'Tab');

    // $id_835
    if (currentContent === targetContent) return;

    // $id_836
    const targetTab = event && event.target ? event.target :
        document.querySelector(`.tab[onclick*="'${tabName}'"]`);

    // $id_837active$id_838
    document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('active'));

    // $id_839active$id_838
    if (targetTab) {
        targetTab.classList.add('active');
        // $id_840
        updateTabSlider(targetTab, true);
    }

    // $id_841
    if (currentContent) {
        // $id_842
        currentContent.style.transition = 'opacity 0.18s ease-out, transform 0.18s ease-out';
        currentContent.style.opacity = '0';
        currentContent.style.transform = 'translateX(-12px)';

        setTimeout(() => {
            currentContent.classList.remove('active');
            currentContent.style.transition = '';
            currentContent.style.opacity = '';
            currentContent.style.transform = '';

            // $id_843
            if (targetContent) {
                // $id_844 active $id_845
                targetContent.style.opacity = '0';
                targetContent.style.transform = 'translateX(12px)';
                targetContent.style.transition = 'none'; // $id_846

                // $id_848 active $id_847
                targetContent.classList.add('active');

                // $id_850 requestAnimationFrame $id_849
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        // $id_851
                        targetContent.style.transition = 'opacity 0.25s ease-out, transform 0.25s ease-out';
                        targetContent.style.opacity = '1';
                        targetContent.style.transform = 'translateX(0)';

                        // $id_852
                        setTimeout(() => {
                            targetContent.style.transition = '';
                            targetContent.style.opacity = '';
                            targetContent.style.transform = '';

                            // $id_853
                            triggerTabDataLoad(tabName);
                        }, 260);
                    });
                });
            }
        }, 180);
    } else {
        // $id_854
        if (targetContent) {
            targetContent.classList.add('active');
            // $id_855
            triggerTabDataLoad(tabName);
        }
    }
}

// $id_856
function triggerTabDataLoad(tabName) {
    if (tabName === 'manage') AppState.creds.refresh();
    if (tabName === 'antigravity-manage') AppState.antigravityCreds.refresh();
    if (tabName === 'config') loadConfig();
    if (tabName === 'logs') connectWebSocket();
}


// =====================================================================
// OAuth$id_857
// =====================================================================
async function startAuth() {
    const projectId = document.getElementById('projectId').value.trim();
    AppState.currentProjectId = projectId || null;

    const btn = document.getElementById('getAuthBtn');
    btn.disabled = true;
    btn.textContent = '$id_858...';

    try {
        const requestBody = projectId ? { project_id: projectId } : {};
        showStatus(projectId ? '$id_861ID$id_862...' : '$id_860ID$id_859...', 'info');

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

            const msg = data.auto_project_detection
                ? '$id_863ID$id_864'
                : `$id_865ID: ${data.detected_project_id}$id_864`;
            showStatus(msg, 'info');
            AppState.authInProgress = true;
        } else {
            showStatus(`$id_806: ${data.error || '$id_866'}`, 'error');
        }
    } catch (error) {
        showStatus(`$id_729: ${error.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '$id_867';
    }
}

async function getCredentials() {
    if (!AppState.authInProgress) {
        showStatus('$id_868', 'error');
        return;
    }

    const btn = document.getElementById('getCredsBtn');
    btn.disabled = true;
    btn.textContent = '$id_870OAuth$id_869...';

    try {
        showStatus('$id_872OAuth$id_871...', 'info');

        const requestBody = AppState.currentProjectId ? { project_id: AppState.currentProjectId } : {};

        const response = await fetch('./auth/callback', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify(requestBody)
        });

        const data = await response.json();

        if (response.ok) {
            document.getElementById('credentialsContent').textContent = JSON.stringify(data.credentials, null, 2);

            const msg = data.auto_detected_project
                ? `✅ $id_873ID$id_875: ${data.credentials.project_id}$id_874: ${data.file_path}`
                : `✅ $id_876: ${data.file_path}`;
            showStatus(msg, 'success');

            document.getElementById('credentialsSection').classList.remove('hidden');
            AppState.authInProgress = false;
        } else if (data.requires_project_selection && data.available_projects) {
            let projectOptions = "$id_877\n\n";
            data.available_projects.forEach((project, index) => {
                projectOptions += `${index + 1}. ${project.name} (${project.project_id})\n`;
            });
            projectOptions += `\n$id_878 (1-${data.available_projects.length}):`;

            const selection = prompt(projectOptions);
            const projectIndex = parseInt(selection) - 1;

            if (projectIndex >= 0 && projectIndex < data.available_projects.length) {
                AppState.currentProjectId = data.available_projects[projectIndex].project_id;
                btn.textContent = '$id_879';
                showStatus(`$id_880...`, 'info');
                setTimeout(() => getCredentials(), 1000);
                return;
            } else {
                showStatus('$id_881', 'error');
            }
        } else if (data.requires_manual_project_id) {
            const userProjectId = prompt('$id_883ID$id_882Google Cloud$id_884ID:');
            if (userProjectId && userProjectId.trim()) {
                AppState.currentProjectId = userProjectId.trim();
                btn.textContent = '$id_879';
                showStatus('$id_885ID$id_886...', 'info');
                setTimeout(() => getCredentials(), 1000);
                return;
            } else {
                showStatus('$id_888ID$id_887ID', 'error');
            }
        } else {
            showStatus(`❌ $id_806: ${data.error || '$id_889'}`, 'error');
        }
    } catch (error) {
        showStatus(`$id_729: ${error.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '$id_890';
    }
}

// =====================================================================
// Antigravity $id_857
// =====================================================================
async function startAntigravityAuth() {
    const btn = document.getElementById('getAntigravityAuthBtn');
    btn.disabled = true;
    btn.textContent = '$id_891...';

    try {
        showStatus('$id_892 Antigravity $id_893...', 'info');

        const response = await fetch('./auth/start', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({ mode: 'antigravity' })
        });

        const data = await response.json();

        if (response.ok) {
            AppState.antigravityAuthState = data.state;
            AppState.antigravityAuthInProgress = true;

            const authUrlLink = document.getElementById('antigravityAuthUrl');
            authUrlLink.href = data.auth_url;
            authUrlLink.textContent = data.auth_url;
            document.getElementById('antigravityAuthUrlSection').classList.remove('hidden');

            showStatus('✅ Antigravity $id_894', 'success');
        } else {
            showStatus(`❌ $id_806: ${data.error || '$id_895'}`, 'error');
        }
    } catch (error) {
        showStatus(`$id_729: ${error.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '$id_712 Antigravity $id_893';
    }
}

async function getAntigravityCredentials() {
    if (!AppState.antigravityAuthInProgress) {
        showStatus('$id_897 Antigravity $id_896', 'error');
        return;
    }

    const btn = document.getElementById('getAntigravityCredsBtn');
    btn.disabled = true;
    btn.textContent = '$id_870OAuth$id_869...';

    try {
        showStatus('$id_872 Antigravity OAuth$id_589...', 'info');

        const response = await fetch('./auth/callback', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({ mode: 'antigravity' })
        });

        const data = await response.json();

        if (response.ok) {
            document.getElementById('antigravityCredsContent').textContent = JSON.stringify(data.credentials, null, 2);
            document.getElementById('antigravityCredsSection').classList.remove('hidden');
            AppState.antigravityAuthInProgress = false;
            showStatus(`✅ Antigravity $id_876: ${data.file_path}`, 'success');
        } else {
            showStatus(`❌ $id_806: ${data.error || '$id_889'}`, 'error');
        }
    } catch (error) {
        showStatus(`$id_729: ${error.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '$id_712 Antigravity $id_100';
    }
}

function downloadAntigravityCredentials() {
    const content = document.getElementById('antigravityCredsContent').textContent;
    const blob = new Blob([content], { type: 'application/json' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `antigravity-credential-${Date.now()}.json`;
    a.click();
    window.URL.revokeObjectURL(url);
}

// =====================================================================
// $id_589URL$id_590
// =====================================================================
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

function toggleAntigravityCallbackUrlSection() {
    const section = document.getElementById('antigravityCallbackUrlSection');
    const icon = document.getElementById('antigravityCallbackUrlToggleIcon');

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

async function processCallbackUrl() {
    const callbackUrl = document.getElementById('callbackUrlInput').value.trim();

    if (!callbackUrl) {
        showStatus('$id_898URL', 'error');
        return;
    }

    if (!callbackUrl.startsWith('http://') && !callbackUrl.startsWith('https://')) {
        showStatus('$id_899URL$id_901http://$id_413https://$id_900', 'error');
        return;
    }

    if (!callbackUrl.includes('code=') || !callbackUrl.includes('state=')) {
        showStatus('❌ $id_903URL$id_904\n1. $id_905Google OAuth$id_907\n2. $id_902URL\n3. URL$id_906code$id_15state$id_226', 'error');
        return;
    }

    showStatus('$id_908URL$id_909...', 'info');

    try {
        const projectId = document.getElementById('projectId')?.value.trim() || null;

        const response = await fetch('./auth/callback-url', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({ callback_url: callbackUrl, project_id: projectId })
        });

        const result = await response.json();

        if (result.credentials) {
            showStatus(result.message || '$id_592URL$id_910', 'success');
            document.getElementById('credentialsContent').innerHTML = '<pre>' + JSON.stringify(result.credentials, null, 2) + '</pre>';
            document.getElementById('credentialsSection').classList.remove('hidden');
        } else if (result.requires_manual_project_id) {
            showStatus('$id_912ID$id_911Google Cloud$id_884ID$id_913', 'error');
        } else if (result.requires_project_selection) {
            let msg = '<br><strong>$id_914</strong><br>';
            result.available_projects.forEach(p => {
                msg += `• ${p.name} (ID: ${p.project_id})<br>`;
            });
            showStatus('$id_915ID$id_212' + msg, 'error');
        } else {
            showStatus(result.error || '$id_592URL$id_916', 'error');
        }

        document.getElementById('callbackUrlInput').value = '';
    } catch (error) {
        showStatus(`$id_592URL$id_916: ${error.message}`, 'error');
    }
}

async function processAntigravityCallbackUrl() {
    const callbackUrl = document.getElementById('antigravityCallbackUrlInput').value.trim();

    if (!callbackUrl) {
        showStatus('$id_898URL', 'error');
        return;
    }

    if (!callbackUrl.startsWith('http://') && !callbackUrl.startsWith('https://')) {
        showStatus('$id_899URL$id_901http://$id_413https://$id_900', 'error');
        return;
    }

    if (!callbackUrl.includes('code=') || !callbackUrl.includes('state=')) {
        showStatus('❌ $id_903URL$id_917code$id_15state$id_226', 'error');
        return;
    }

    showStatus('$id_908URL$id_712 Antigravity $id_100...', 'info');

    try {
        const response = await fetch('./auth/callback-url', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({ callback_url: callbackUrl, mode: 'antigravity' })
        });

        const result = await response.json();

        if (result.credentials) {
            showStatus(result.message || '$id_592URL$id_712 Antigravity $id_918', 'success');
            document.getElementById('antigravityCredsContent').textContent = JSON.stringify(result.credentials, null, 2);
            document.getElementById('antigravityCredsSection').classList.remove('hidden');
        } else {
            showStatus(result.error || '$id_592URL$id_712 Antigravity $id_919', 'error');
        }

        document.getElementById('antigravityCallbackUrlInput').value = '';
    } catch (error) {
        showStatus(`$id_592URL$id_712 Antigravity $id_919: ${error.message}`, 'error');
    }
}

// =====================================================================
// $id_920HTML$id_921
// =====================================================================
// $id_922
function refreshCredsStatus() { AppState.creds.refresh(); }
function applyStatusFilter() { AppState.creds.applyStatusFilter(); }
function changePage(direction) { AppState.creds.changePage(direction); }
function changePageSize() { AppState.creds.changePageSize(); }
function toggleFileSelection(filename) {
    if (AppState.creds.selectedFiles.has(filename)) {
        AppState.creds.selectedFiles.delete(filename);
    } else {
        AppState.creds.selectedFiles.add(filename);
    }
    AppState.creds.updateBatchControls();
}
function toggleSelectAll() {
    const checkbox = document.getElementById('selectAllCheckbox');
    const checkboxes = document.querySelectorAll('.file-checkbox');

    if (checkbox.checked) {
        checkboxes.forEach(cb => AppState.creds.selectedFiles.add(cb.getAttribute('data-filename')));
    } else {
        AppState.creds.selectedFiles.clear();
    }
    checkboxes.forEach(cb => cb.checked = checkbox.checked);
    AppState.creds.updateBatchControls();
}
function batchAction(action) { AppState.creds.batchAction(action); }
function downloadCred(filename) {
    fetch(`./creds/download/${filename}`, { headers: { 'Authorization': `Bearer ${AppState.authToken}` } })
        .then(r => r.ok ? r.blob() : Promise.reject())
        .then(blob => {
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            a.click();
            window.URL.revokeObjectURL(url);
            showStatus(`$id_923: ${filename}`, 'success');
        })
        .catch(() => showStatus(`$id_924: ${filename}`, 'error'));
}
async function downloadAllCreds() {
    try {
        const response = await fetch('./creds/download-all', {
            headers: { 'Authorization': `Bearer ${AppState.authToken}` }
        });
        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'credentials.zip';
            a.click();
            window.URL.revokeObjectURL(url);
            showStatus('$id_925', 'success');
        }
    } catch (error) {
        showStatus(`$id_926: ${error.message}`, 'error');
    }
}

// Antigravity$id_705
function refreshAntigravityCredsList() { AppState.antigravityCreds.refresh(); }
function applyAntigravityStatusFilter() { AppState.antigravityCreds.applyStatusFilter(); }
function changeAntigravityPage(direction) { AppState.antigravityCreds.changePage(direction); }
function changeAntigravityPageSize() { AppState.antigravityCreds.changePageSize(); }
function toggleAntigravityFileSelection(filename) {
    if (AppState.antigravityCreds.selectedFiles.has(filename)) {
        AppState.antigravityCreds.selectedFiles.delete(filename);
    } else {
        AppState.antigravityCreds.selectedFiles.add(filename);
    }
    AppState.antigravityCreds.updateBatchControls();
}
function toggleSelectAllAntigravity() {
    const checkbox = document.getElementById('selectAllAntigravityCheckbox');
    const checkboxes = document.querySelectorAll('.antigravityFile-checkbox');

    if (checkbox.checked) {
        checkboxes.forEach(cb => AppState.antigravityCreds.selectedFiles.add(cb.getAttribute('data-filename')));
    } else {
        AppState.antigravityCreds.selectedFiles.clear();
    }
    checkboxes.forEach(cb => cb.checked = checkbox.checked);
    AppState.antigravityCreds.updateBatchControls();
}
function batchAntigravityAction(action) { AppState.antigravityCreds.batchAction(action); }
function downloadAntigravityCred(filename) {
    fetch(`./creds/download/${filename}?mode=antigravity`, { headers: getAuthHeaders() })
        .then(r => r.ok ? r.blob() : Promise.reject())
        .then(blob => {
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            a.click();
            window.URL.revokeObjectURL(url);
            showStatus(`✅ $id_927: ${filename}`, 'success');
        })
        .catch(() => showStatus(`$id_924: ${filename}`, 'error'));
}
function deleteAntigravityCred(filename) {
    if (confirm(`$id_814 ${filename} $id_928`)) {
        AppState.antigravityCreds.action(filename, 'delete');
    }
}
async function downloadAllAntigravityCreds() {
    try {
        const response = await fetch('./creds/download-all?mode=antigravity', { headers: getAuthHeaders() });
        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `antigravity_credentials_${Date.now()}.zip`;
            a.click();
            window.URL.revokeObjectURL(url);
            showStatus('✅ $id_930Antigravity$id_929', 'success');
        }
    } catch (error) {
        showStatus(`$id_729: ${error.message}`, 'error');
    }
}

// $id_706
function handleFileSelect(event) { AppState.uploadFiles.handleFileSelect(event); }
function removeFile(index) { AppState.uploadFiles.removeFile(index); }
function clearFiles() { AppState.uploadFiles.clearFiles(); }
function uploadFiles() { AppState.uploadFiles.upload(); }

function handleAntigravityFileSelect(event) { AppState.antigravityUploadFiles.handleFileSelect(event); }
function handleAntigravityFileDrop(event) {
    event.preventDefault();
    event.currentTarget.style.borderColor = '#007bff';
    event.currentTarget.style.backgroundColor = '#f8f9fa';
    AppState.antigravityUploadFiles.addFiles(Array.from(event.dataTransfer.files));
}
function removeAntigravityFile(index) { AppState.antigravityUploadFiles.removeFile(index); }
function clearAntigravityFiles() { AppState.antigravityUploadFiles.clearFiles(); }
function uploadAntigravityFiles() { AppState.antigravityUploadFiles.upload(); }

// $id_931
// $id_932
function updateEmailDisplay(filename, email, managerType = 'normal') {
    // $id_933
    const containerId = managerType === 'antigravity' ? 'antigravityCredsList' : 'credsList';
    const container = document.getElementById(containerId);
    if (!container) return false;

    // $id_935 data-filename $id_934
    const checkbox = container.querySelector(`input[data-filename="${filename}"]`);
    if (!checkbox) return false;

    // $id_936 cred-card $id_713
    const card = checkbox.closest('.cred-card');
    if (!card) return false;

    // $id_937
    const emailDiv = card.querySelector('.cred-email');
    if (emailDiv) {
        emailDiv.textContent = email;
        emailDiv.style.color = '#666';
        emailDiv.style.fontStyle = 'normal';
        return true;
    }
    return false;
}

async function fetchUserEmail(filename) {
    try {
        showStatus('$id_938...', 'info');
        const response = await fetch(`./creds/fetch-email/${encodeURIComponent(filename)}`, {
            method: 'POST',
            headers: getAuthHeaders()
        });
        const data = await response.json();
        if (response.ok && data.user_email) {
            showStatus(`$id_939: ${data.user_email}`, 'success');
            // $id_940
            updateEmailDisplay(filename, data.user_email, 'normal');
        } else {
            showStatus(data.message || '$id_941', 'error');
        }
    } catch (error) {
        showStatus(`$id_942: ${error.message}`, 'error');
    }
}

async function fetchAntigravityUserEmail(filename) {
    try {
        showStatus('$id_938...', 'info');
        const response = await fetch(`./creds/fetch-email/${encodeURIComponent(filename)}?mode=antigravity`, {
            method: 'POST',
            headers: getAuthHeaders()
        });
        const data = await response.json();
        if (response.ok && data.user_email) {
            showStatus(`$id_939: ${data.user_email}`, 'success');
            // $id_940
            updateEmailDisplay(filename, data.user_email, 'antigravity');
        } else {
            showStatus(data.message || '$id_941', 'error');
        }
    } catch (error) {
        showStatus(`$id_942: ${error.message}`, 'error');
    }
}

async function verifyProjectId(filename) {
    try {
        // $id_943
        showStatus('🔍 $id_944Project ID$id_945...', 'info');

        const response = await fetch(`./creds/verify-project/${encodeURIComponent(filename)}`, {
            method: 'POST',
            headers: getAuthHeaders()
        });
        const data = await response.json();

        if (response.ok && data.success) {
            // $id_946Project ID
            const successMsg = `✅ $id_947\n$id_112: ${filename}\nProject ID: ${data.project_id}\n\n${data.message}`;
            showStatus(successMsg.replace(/\n/g, '<br>'), 'success');

            // $id_948
            alert(`✅ $id_947\n\n$id_112: ${filename}\nProject ID: ${data.project_id}\n\n${data.message}`);

            await AppState.creds.refresh();
        } else {
            // $id_949
            const errorMsg = data.message || '$id_950';
            showStatus(`❌ ${errorMsg}`, 'error');
            alert(`❌ $id_950\n\n${errorMsg}`);
        }
    } catch (error) {
        const errorMsg = `$id_950: ${error.message}`;
        showStatus(`❌ ${errorMsg}`, 'error');
        alert(`❌ ${errorMsg}`);
    }
}

async function verifyAntigravityProjectId(filename) {
    try {
        // $id_943
        showStatus('🔍 $id_944Antigravity Project ID$id_945...', 'info');

        const response = await fetch(`./creds/verify-project/${encodeURIComponent(filename)}?mode=antigravity`, {
            method: 'POST',
            headers: getAuthHeaders()
        });
        const data = await response.json();

        if (response.ok && data.success) {
            // $id_946Project ID
            const successMsg = `✅ $id_947\n$id_112: ${filename}\nProject ID: ${data.project_id}\n\n${data.message}`;
            showStatus(successMsg.replace(/\n/g, '<br>'), 'success');

            // $id_948
            alert(`✅ Antigravity$id_947\n\n$id_112: ${filename}\nProject ID: ${data.project_id}\n\n${data.message}`);

            await AppState.antigravityCreds.refresh();
        } else {
            // $id_949
            const errorMsg = data.message || '$id_950';
            showStatus(`❌ ${errorMsg}`, 'error');
            alert(`❌ $id_950\n\n${errorMsg}`);
        }
    } catch (error) {
        const errorMsg = `$id_950: ${error.message}`;
        showStatus(`❌ ${errorMsg}`, 'error');
        alert(`❌ ${errorMsg}`);
    }
}

async function toggleAntigravityQuotaDetails(pathId) {
    const quotaDetails = document.getElementById('quota-' + pathId);
    if (!quotaDetails) return;

    // $id_951
    const isShowing = quotaDetails.style.display === 'block';

    if (isShowing) {
        // $id_952
        quotaDetails.style.display = 'none';
    } else {
        // $id_953
        quotaDetails.style.display = 'block';

        const contentDiv = quotaDetails.querySelector('.cred-quota-content');
        const filename = contentDiv.getAttribute('data-filename');
        const loaded = contentDiv.getAttribute('data-loaded');

        // $id_954
        if (loaded === 'false' && filename) {
            contentDiv.innerHTML = '<div style="text-align: center; padding: 20px; color: #666;">📊 $id_955...</div>';

            try {
                const response = await fetch(`./creds/quota/${encodeURIComponent(filename)}?mode=antigravity`, {
                    method: 'GET',
                    headers: getAuthHeaders()
                });
                const data = await response.json();

                if (response.ok && data.success) {
                    // $id_956
                    const models = data.models || {};

                    if (Object.keys(models).length === 0) {
                        contentDiv.innerHTML = `
                            <div style="text-align: center; padding: 20px; color: #999;">
                                <div style="font-size: 48px; margin-bottom: 10px;">📊</div>
                                <div>$id_957</div>
                            </div>
                        `;
                    } else {
                        let quotaHTML = `
                            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 15px; border-radius: 8px 8px 0 0; margin: -10px -10px 15px -10px;">
                                <h4 style="margin: 0; font-size: 16px; display: flex; align-items: center; gap: 8px;">
                                    <span style="font-size: 20px;">📊</span>
                                    <span>$id_958</span>
                                </h4>
                                <div style="font-size: 12px; opacity: 0.9; margin-top: 5px;">$id_112: ${filename}</div>
                            </div>
                            <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px;">
                        `;

                        for (const [modelName, quotaData] of Object.entries(models)) {
                            // $id_959 (0-1)$id_960
                            const remainingFraction = quotaData.remaining || 0;
                            const resetTime = quotaData.resetTime || 'N/A';

                            // $id_9611 - $id_962
                            const usedPercentage = Math.round((1 - remainingFraction) * 100);
                            const remainingPercentage = Math.round(remainingFraction * 100);

                            // $id_963
                            let percentageColor = '#28a745'; // $id_964
                            if (usedPercentage >= 90) percentageColor = '#dc3545'; // $id_965
                            else if (usedPercentage >= 70) percentageColor = '#ffc107'; // $id_966
                            else if (usedPercentage >= 50) percentageColor = '#17a2b8'; // $id_967

                            quotaHTML += `
                                <div style="background: white; border-left: 4px solid ${percentageColor}; border-radius: 4px; padding: 8px 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                                        <div style="font-weight: bold; color: #333; font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; margin-right: 8px;" title="${modelName} - $id_968${remainingPercentage}% - ${resetTime}">
                                            ${modelName}
                                        </div>
                                        <div style="font-size: 13px; font-weight: bold; color: ${percentageColor}; white-space: nowrap;">
                                            ${remainingPercentage}%
                                        </div>
                                    </div>
                                    <div style="width: 100%; height: 8px; background-color: #e9ecef; border-radius: 4px; overflow: hidden; margin-bottom: 4px;">
                                        <div style="width: ${usedPercentage}%; height: 100%; background-color: ${percentageColor}; transition: width 0.3s ease;"></div>
                                    </div>
                                    <div style="font-size: 10px; color: #666; text-align: right;">
                                        ${resetTime !== 'N/A' ? '🔄 ' + resetTime : ''}
                                    </div>
                                </div>
                            `;
                        }

                        quotaHTML += '</div>';
                        contentDiv.innerHTML = quotaHTML;
                    }

                    contentDiv.setAttribute('data-loaded', 'true');
                    showStatus('✅ $id_969', 'success');
                } else {
                    // $id_970
                    const errorMsg = data.error || '$id_971';
                    contentDiv.innerHTML = `
                        <div style="text-align: center; padding: 20px; color: #dc3545;">
                            <div style="font-size: 48px; margin-bottom: 10px;">❌</div>
                            <div style="font-weight: bold; margin-bottom: 5px;">$id_971</div>
                            <div style="font-size: 13px; color: #666;">${errorMsg}</div>
                        </div>
                    `;
                    showStatus(`❌ ${errorMsg}`, 'error');
                }
            } catch (error) {
                contentDiv.innerHTML = `
                    <div style="text-align: center; padding: 20px; color: #dc3545;">
                        <div style="font-size: 48px; margin-bottom: 10px;">❌</div>
                        <div style="font-weight: bold; margin-bottom: 5px;">$id_729</div>
                        <div style="font-size: 13px; color: #666;">${error.message}</div>
                    </div>
                `;
                showStatus(`❌ $id_971: ${error.message}`, 'error');
            }
        }
    }
}

async function batchVerifyProjectIds() {
    const selectedFiles = Array.from(AppState.creds.selectedFiles);
    if (selectedFiles.length === 0) {
        showStatus('❌ $id_972', 'error');
        alert('$id_972');
        return;
    }

    if (!confirm(`$id_974 ${selectedFiles.length} $id_975Project ID$id_928\n\n$id_973`)) {
        return;
    }

    showStatus(`🔍 $id_977 ${selectedFiles.length} $id_976...`, 'info');

    // $id_978
    const promises = selectedFiles.map(async (filename) => {
        try {
            const response = await fetch(`./creds/verify-project/${encodeURIComponent(filename)}`, {
                method: 'POST',
                headers: getAuthHeaders()
            });
            const data = await response.json();

            if (response.ok && data.success) {
                return { success: true, filename, projectId: data.project_id, message: data.message };
            } else {
                return { success: false, filename, error: data.message || '$id_979' };
            }
        } catch (error) {
            return { success: false, filename, error: error.message };
        }
    });

    // $id_980
    const results = await Promise.all(promises);

    // $id_981
    let successCount = 0;
    let failCount = 0;
    const resultMessages = [];

    results.forEach(result => {
        if (result.success) {
            successCount++;
            resultMessages.push(`✅ ${result.filename}: ${result.projectId}`);
        } else {
            failCount++;
            resultMessages.push(`❌ ${result.filename}: ${result.error}`);
        }
    });

    await AppState.creds.refresh();

    const summary = `$id_982\n\n$id_984: ${successCount} $id_723\n$id_979: ${failCount} $id_723\n$id_985: ${selectedFiles.length} $id_723\n\n$id_983:\n${resultMessages.join('\n')}`;

    if (failCount === 0) {
        showStatus(`✅ $id_986 ${successCount}/${selectedFiles.length} $id_987`, 'success');
    } else if (successCount === 0) {
        showStatus(`❌ $id_988 ${failCount}/${selectedFiles.length} $id_987`, 'error');
    } else {
        showStatus(`⚠️ $id_989 ${successCount}/${selectedFiles.length} $id_990 ${failCount} $id_723`, 'info');
    }

    console.log(summary);
    alert(summary);
}

async function batchVerifyAntigravityProjectIds() {
    const selectedFiles = Array.from(AppState.antigravityCreds.selectedFiles);
    if (selectedFiles.length === 0) {
        showStatus('❌ $id_991Antigravity$id_100', 'error');
        alert('$id_991Antigravity$id_100');
        return;
    }

    if (!confirm(`$id_974 ${selectedFiles.length} $id_723Antigravity$id_992Project ID$id_928\n\n$id_973`)) {
        return;
    }

    showStatus(`🔍 $id_977 ${selectedFiles.length} $id_723Antigravity$id_993...`, 'info');

    // $id_978
    const promises = selectedFiles.map(async (filename) => {
        try {
            const response = await fetch(`./creds/verify-project/${encodeURIComponent(filename)}?mode=antigravity`, {
                method: 'POST',
                headers: getAuthHeaders()
            });
            const data = await response.json();

            if (response.ok && data.success) {
                return { success: true, filename, projectId: data.project_id, message: data.message };
            } else {
                return { success: false, filename, error: data.message || '$id_979' };
            }
        } catch (error) {
            return { success: false, filename, error: error.message };
        }
    });

    // $id_980
    const results = await Promise.all(promises);

    // $id_981
    let successCount = 0;
    let failCount = 0;
    const resultMessages = [];

    results.forEach(result => {
        if (result.success) {
            successCount++;
            resultMessages.push(`✅ ${result.filename}: ${result.projectId}`);
        } else {
            failCount++;
            resultMessages.push(`❌ ${result.filename}: ${result.error}`);
        }
    });

    await AppState.antigravityCreds.refresh();

    const summary = `Antigravity$id_982\n\n$id_984: ${successCount} $id_723\n$id_979: ${failCount} $id_723\n$id_985: ${selectedFiles.length} $id_723\n\n$id_983:\n${resultMessages.join('\n')}`;

    if (failCount === 0) {
        showStatus(`✅ $id_986 ${successCount}/${selectedFiles.length} $id_723Antigravity$id_100`, 'success');
    } else if (successCount === 0) {
        showStatus(`❌ $id_988 ${failCount}/${selectedFiles.length} $id_723Antigravity$id_100`, 'error');
    } else {
        showStatus(`⚠️ $id_989 ${successCount}/${selectedFiles.length} $id_990 ${failCount} $id_723`, 'info');
    }

    console.log(summary);
    alert(summary);
}


async function refreshAllEmails() {
    if (!confirm('$id_994')) return;

    try {
        showStatus('$id_995...', 'info');
        const response = await fetch('./creds/refresh-all-emails', {
            method: 'POST',
            headers: getAuthHeaders()
        });
        const data = await response.json();
        if (response.ok) {
            showStatus(`$id_996 ${data.success_count}/${data.total_count} $id_997`, 'success');
            await AppState.creds.refresh();
        } else {
            showStatus(data.message || '$id_998', 'error');
        }
    } catch (error) {
        showStatus(`$id_999: ${error.message}`, 'error');
    }
}

async function refreshAllAntigravityEmails() {
    if (!confirm('$id_1001Antigravity$id_1000')) return;

    try {
        showStatus('$id_995...', 'info');
        const response = await fetch('./creds/refresh-all-emails?mode=antigravity', {
            method: 'POST',
            headers: getAuthHeaders()
        });
        const data = await response.json();
        if (response.ok) {
            showStatus(`$id_996 ${data.success_count}/${data.total_count} $id_997`, 'success');
            await AppState.antigravityCreds.refresh();
        } else {
            showStatus(data.message || '$id_998', 'error');
        }
    } catch (error) {
        showStatus(`$id_999: ${error.message}`, 'error');
    }
}

async function deduplicateByEmail() {
    if (!confirm('$id_1003\n\n$id_1002\n$id_1004')) return;

    try {
        showStatus('$id_1005...', 'info');
        const response = await fetch('./creds/deduplicate-by-email', {
            method: 'POST',
            headers: getAuthHeaders()
        });
        const data = await response.json();
        if (response.ok) {
            const msg = `$id_1007 ${data.deleted_count} $id_1006 ${data.kept_count} $id_1009${data.unique_emails_count} $id_1008`;
            showStatus(msg, 'success');
            await AppState.creds.refresh();
            
            // $id_1010
            if (data.duplicate_groups && data.duplicate_groups.length > 0) {
                let details = '$id_1011\n\n';
                data.duplicate_groups.forEach(group => {
                    details += `$id_1013: ${group.email}\n$id_1012: ${group.kept_file}\n$id_753: ${group.deleted_files.join(', ')}\n\n`;
                });
                console.log(details);
            }
        } else {
            showStatus(data.message || '$id_1014', 'error');
        }
    } catch (error) {
        showStatus(`$id_1015: ${error.message}`, 'error');
    }
}

async function deduplicateAntigravityByEmail() {
    if (!confirm('$id_1017Antigravity$id_1016\n\n$id_1002\n$id_1004')) return;

    try {
        showStatus('$id_1005...', 'info');
        const response = await fetch('./creds/deduplicate-by-email?mode=antigravity', {
            method: 'POST',
            headers: getAuthHeaders()
        });
        const data = await response.json();
        if (response.ok) {
            const msg = `$id_1007 ${data.deleted_count} $id_1006 ${data.kept_count} $id_1009${data.unique_emails_count} $id_1008`;
            showStatus(msg, 'success');
            await AppState.antigravityCreds.refresh();
            
            // $id_1010
            if (data.duplicate_groups && data.duplicate_groups.length > 0) {
                let details = '$id_1011\n\n';
                data.duplicate_groups.forEach(group => {
                    details += `$id_1013: ${group.email}\n$id_1012: ${group.kept_file}\n$id_753: ${group.deleted_files.join(', ')}\n\n`;
                });
                console.log(details);
            }
        } else {
            showStatus(data.message || '$id_1014', 'error');
        }
    } catch (error) {
        showStatus(`$id_1015: ${error.message}`, 'error');
    }
}

// =====================================================================
// WebSocket$id_1018
// =====================================================================
function connectWebSocket() {
    if (AppState.logWebSocket && AppState.logWebSocket.readyState === WebSocket.OPEN) {
        showStatus('WebSocket$id_1019', 'info');
        return;
    }

    try {
        const wsPath = new URL('./logs/stream', window.location.href).href;
        const wsUrl = wsPath.replace(/^http/, 'ws');

        // $id_848 token $id_1020
        const wsUrlWithAuth = `${wsUrl}?token=${encodeURIComponent(AppState.authToken)}`;

        document.getElementById('connectionStatusText').textContent = '$id_1021...';
        document.getElementById('logConnectionStatus').className = 'status info';

        AppState.logWebSocket = new WebSocket(wsUrlWithAuth);

        AppState.logWebSocket.onopen = () => {
            document.getElementById('connectionStatusText').textContent = '$id_1022';
            document.getElementById('logConnectionStatus').className = 'status success';
            showStatus('$id_1023', 'success');
            clearLogsDisplay();
        };

        AppState.logWebSocket.onmessage = (event) => {
            const logLine = event.data;
            if (logLine.trim()) {
                AppState.allLogs.push(logLine);
                if (AppState.allLogs.length > 1000) {
                    AppState.allLogs = AppState.allLogs.slice(-1000);
                }
                filterLogs();
                if (document.getElementById('autoScroll').checked) {
                    const logContainer = document.getElementById('logContainer');
                    logContainer.scrollTop = logContainer.scrollHeight;
                }
            }
        };

        AppState.logWebSocket.onclose = () => {
            document.getElementById('connectionStatusText').textContent = '$id_1024';
            document.getElementById('logConnectionStatus').className = 'status error';
            showStatus('$id_1025', 'info');
        };

        AppState.logWebSocket.onerror = (error) => {
            document.getElementById('connectionStatusText').textContent = '$id_1026';
            document.getElementById('logConnectionStatus').className = 'status error';
            showStatus('$id_1027: ' + error, 'error');
        };
    } catch (error) {
        showStatus('$id_1029WebSocket$id_1028: ' + error.message, 'error');
        document.getElementById('connectionStatusText').textContent = '$id_1028';
        document.getElementById('logConnectionStatus').className = 'status error';
    }
}

function disconnectWebSocket() {
    if (AppState.logWebSocket) {
        AppState.logWebSocket.close();
        AppState.logWebSocket = null;
        document.getElementById('connectionStatusText').textContent = '$id_1030';
        document.getElementById('logConnectionStatus').className = 'status info';
        showStatus('$id_1031', 'info');
    }
}

function clearLogsDisplay() {
    AppState.allLogs = [];
    AppState.filteredLogs = [];
    document.getElementById('logContent').textContent = '$id_1032...';
}

async function downloadLogs() {
    try {
        const response = await fetch('./logs/download', { headers: getAuthHeaders() });

        if (response.ok) {
            const contentDisposition = response.headers.get('Content-Disposition');
            let filename = 'gcli2api_logs.txt';
            if (contentDisposition) {
                const match = contentDisposition.match(/filename=(.+)/);
                if (match) filename = match[1];
            }

            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            a.click();
            window.URL.revokeObjectURL(url);

            showStatus(`$id_1033: ${filename}`, 'success');
        } else {
            const data = await response.json();
            showStatus(`$id_1034: ${data.detail || data.error || '$id_727'}`, 'error');
        }
    } catch (error) {
        showStatus(`$id_1035: ${error.message}`, 'error');
    }
}

async function clearLogs() {
    try {
        const response = await fetch('./logs/clear', {
            method: 'POST',
            headers: getAuthHeaders()
        });

        const data = await response.json();

        if (response.ok) {
            clearLogsDisplay();
            showStatus(data.message, 'success');
        } else {
            showStatus(`$id_1036: ${data.detail || data.error || '$id_727'}`, 'error');
        }
    } catch (error) {
        clearLogsDisplay();
        showStatus(`$id_1037: ${error.message}`, 'error');
    }
}

function filterLogs() {
    const filter = document.getElementById('logLevelFilter').value;
    AppState.currentLogFilter = filter;

    if (filter === 'all') {
        AppState.filteredLogs = [...AppState.allLogs];
    } else {
        AppState.filteredLogs = AppState.allLogs.filter(log => log.toUpperCase().includes(filter));
    }

    displayLogs();
}

function displayLogs() {
    const logContent = document.getElementById('logContent');
    if (AppState.filteredLogs.length === 0) {
        logContent.textContent = AppState.currentLogFilter === 'all' ?
            '$id_1039...' : `$id_1040${AppState.currentLogFilter}$id_1038...`;
    } else {
        logContent.textContent = AppState.filteredLogs.join('\n');
    }
}

// =====================================================================
// $id_1041
// =====================================================================
async function checkEnvCredsStatus() {
    const loading = document.getElementById('envStatusLoading');
    const content = document.getElementById('envStatusContent');

    try {
        loading.style.display = 'block';
        content.classList.add('hidden');

        const response = await fetch('./auth/env-creds-status', { headers: getAuthHeaders() });
        const data = await response.json();

        if (response.ok) {
            const envVarsList = document.getElementById('envVarsList');
            envVarsList.textContent = Object.keys(data.available_env_vars).length > 0
                ? Object.keys(data.available_env_vars).join(', ')
                : '$id_1042GCLI_CREDS_*$id_107';

            const autoLoadStatus = document.getElementById('autoLoadStatus');
            autoLoadStatus.textContent = data.auto_load_enabled ? '✅ $id_790' : '❌ $id_1043';
            autoLoadStatus.style.color = data.auto_load_enabled ? '#28a745' : '#dc3545';

            document.getElementById('envFilesCount').textContent = `${data.existing_env_files_count} $id_762`;

            const envFilesList = document.getElementById('envFilesList');
            envFilesList.textContent = data.existing_env_files.length > 0
                ? data.existing_env_files.join(', ')
                : '$id_39';

            content.classList.remove('hidden');
            showStatus('$id_1044', 'success');
        } else {
            showStatus(`$id_1045: ${data.detail || data.error || '$id_727'}`, 'error');
        }
    } catch (error) {
        showStatus(`$id_729: ${error.message}`, 'error');
    } finally {
        loading.style.display = 'none';
    }
}

async function loadEnvCredentials() {
    try {
        showStatus('$id_1046...', 'info');

        const response = await fetch('./auth/load-env-creds', {
            method: 'POST',
            headers: getAuthHeaders()
        });

        const data = await response.json();

        if (response.ok) {
            if (data.loaded_count > 0) {
                showStatus(`✅ $id_1048 ${data.loaded_count}/${data.total_count} $id_1047`, 'success');
                setTimeout(() => checkEnvCredsStatus(), 1000);
            } else {
                showStatus(`⚠️ ${data.message}`, 'info');
            }
        } else {
            showStatus(`$id_1049: ${data.detail || data.error || '$id_727'}`, 'error');
        }
    } catch (error) {
        showStatus(`$id_729: ${error.message}`, 'error');
    }
}

async function clearEnvCredentials() {
    if (!confirm('$id_1050\n$id_1051 "env-" $id_1052')) {
        return;
    }

    try {
        showStatus('$id_1053...', 'info');

        const response = await fetch('./auth/env-creds', {
            method: 'DELETE',
            headers: getAuthHeaders()
        });

        const data = await response.json();

        if (response.ok) {
            showStatus(`✅ $id_1055 ${data.deleted_count} $id_1054`, 'success');
            setTimeout(() => checkEnvCredsStatus(), 1000);
        } else {
            showStatus(`$id_1056: ${data.detail || data.error || '$id_727'}`, 'error');
        }
    } catch (error) {
        showStatus(`$id_729: ${error.message}`, 'error');
    }
}

// =====================================================================
// $id_707
// =====================================================================
async function loadConfig() {
    const loading = document.getElementById('configLoading');
    const form = document.getElementById('configForm');

    try {
        loading.style.display = 'block';
        form.classList.add('hidden');

        const response = await fetch('./config/get', { headers: getAuthHeaders() });
        const data = await response.json();

        if (response.ok) {
            AppState.currentConfig = data.config;
            AppState.envLockedFields = new Set(data.env_locked || []);

            populateConfigForm();
            form.classList.remove('hidden');
            showStatus('$id_1057', 'success');
        } else {
            showStatus(`$id_1058: ${data.detail || data.error || '$id_727'}`, 'error');
        }
    } catch (error) {
        showStatus(`$id_729: ${error.message}`, 'error');
    } finally {
        loading.style.display = 'none';
    }
}

function populateConfigForm() {
    const c = AppState.currentConfig;

    setConfigField('host', c.host || '0.0.0.0');
    setConfigField('port', c.port || 7861);
    setConfigField('configApiPassword', c.api_password || '');
    setConfigField('configPanelPassword', c.panel_password || '');
    setConfigField('configPassword', c.password || 'pwd');
    setConfigField('credentialsDir', c.credentials_dir || '');
    setConfigField('proxy', c.proxy || '');
    setConfigField('codeAssistEndpoint', c.code_assist_endpoint || '');
    setConfigField('oauthProxyUrl', c.oauth_proxy_url || '');
    setConfigField('googleapisProxyUrl', c.googleapis_proxy_url || '');
    setConfigField('resourceManagerApiUrl', c.resource_manager_api_url || '');
    setConfigField('serviceUsageApiUrl', c.service_usage_api_url || '');
    setConfigField('antigravityApiUrl', c.antigravity_api_url || '');

    document.getElementById('autoBanEnabled').checked = Boolean(c.auto_ban_enabled);
    setConfigField('autoBanErrorCodes', (c.auto_ban_error_codes || []).join(','));
    setConfigField('callsPerRotation', c.calls_per_rotation || 10);

    document.getElementById('retry429Enabled').checked = Boolean(c.retry_429_enabled);
    setConfigField('retry429MaxRetries', c.retry_429_max_retries || 20);
    setConfigField('retry429Interval', c.retry_429_interval || 0.1);

    document.getElementById('compatibilityModeEnabled').checked = Boolean(c.compatibility_mode_enabled);
    document.getElementById('returnThoughtsToFrontend').checked = Boolean(c.return_thoughts_to_frontend !== false);
    document.getElementById('antigravityStream2nostream').checked = Boolean(c.antigravity_stream2nostream !== false);

    setConfigField('antiTruncationMaxAttempts', c.anti_truncation_max_attempts || 3);
}

function setConfigField(fieldId, value) {
    const field = document.getElementById(fieldId);
    if (field) {
        field.value = value;
        const configKey = fieldId.replace(/([A-Z])/g, '_$1').toLowerCase();
        if (AppState.envLockedFields.has(configKey)) {
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
        const getValue = (id, def = '') => document.getElementById(id)?.value.trim() || def;
        const getInt = (id, def = 0) => parseInt(document.getElementById(id)?.value) || def;
        const getFloat = (id, def = 0.0) => parseFloat(document.getElementById(id)?.value) || def;
        const getChecked = (id, def = false) => document.getElementById(id)?.checked || def;

        const config = {
            host: getValue('host', '0.0.0.0'),
            port: getInt('port', 7861),
            api_password: getValue('configApiPassword'),
            panel_password: getValue('configPanelPassword'),
            password: getValue('configPassword', 'pwd'),
            code_assist_endpoint: getValue('codeAssistEndpoint'),
            credentials_dir: getValue('credentialsDir'),
            proxy: getValue('proxy'),
            oauth_proxy_url: getValue('oauthProxyUrl'),
            googleapis_proxy_url: getValue('googleapisProxyUrl'),
            resource_manager_api_url: getValue('resourceManagerApiUrl'),
            service_usage_api_url: getValue('serviceUsageApiUrl'),
            antigravity_api_url: getValue('antigravityApiUrl'),
            auto_ban_enabled: getChecked('autoBanEnabled'),
            auto_ban_error_codes: getValue('autoBanErrorCodes').split(',')
                .map(c => parseInt(c.trim())).filter(c => !isNaN(c)),
            calls_per_rotation: getInt('callsPerRotation', 10),
            retry_429_enabled: getChecked('retry429Enabled'),
            retry_429_max_retries: getInt('retry429MaxRetries', 20),
            retry_429_interval: getFloat('retry429Interval', 0.1),
            compatibility_mode_enabled: getChecked('compatibilityModeEnabled'),
            return_thoughts_to_frontend: getChecked('returnThoughtsToFrontend'),
            antigravity_stream2nostream: getChecked('antigravityStream2nostream'),
            anti_truncation_max_attempts: getInt('antiTruncationMaxAttempts', 3)
        };

        const response = await fetch('./config/save', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({ config })
        });

        const data = await response.json();

        if (response.ok) {
            let message = '$id_1059';

            if (data.hot_updated && data.hot_updated.length > 0) {
                message += `$id_1060: ${data.hot_updated.join(', ')}`;
            }

            if (data.restart_required && data.restart_required.length > 0) {
                message += `\n⚠️ $id_1061: ${data.restart_notice}`;
                showStatus(message, 'info');
            } else {
                showStatus(message, 'success');
            }

            setTimeout(() => loadConfig(), 1000);
        } else {
            showStatus(`$id_1062: ${data.detail || data.error || '$id_727'}`, 'error');
        }
    } catch (error) {
        showStatus(`$id_729: ${error.message}`, 'error');
    }
}

// $id_1063
const mirrorUrls = {
    codeAssistEndpoint: 'https://gcli-api.sukaka.top/cloudcode-pa',
    oauthProxyUrl: 'https://gcli-api.sukaka.top/oauth2',
    googleapisProxyUrl: 'https://gcli-api.sukaka.top/googleapis',
    resourceManagerApiUrl: 'https://gcli-api.sukaka.top/cloudresourcemanager',
    serviceUsageApiUrl: 'https://gcli-api.sukaka.top/serviceusage',
    antigravityApiUrl: 'https://gcli-api.sukaka.top/daily-cloudcode-pa'
};

const officialUrls = {
    codeAssistEndpoint: 'https://cloudcode-pa.googleapis.com',
    oauthProxyUrl: 'https://oauth2.googleapis.com',
    googleapisProxyUrl: 'https://www.googleapis.com',
    resourceManagerApiUrl: 'https://cloudresourcemanager.googleapis.com',
    serviceUsageApiUrl: 'https://serviceusage.googleapis.com',
    antigravityApiUrl: 'https://daily-cloudcode-pa.sandbox.googleapis.com'
};

function useMirrorUrls() {
    if (confirm('$id_1064')) {
        for (const [fieldId, url] of Object.entries(mirrorUrls)) {
            const field = document.getElementById(fieldId);
            if (field && !field.disabled) field.value = url;
        }
        showStatus('✅ $id_1065"$id_612"$id_1066', 'success');
    }
}

function restoreOfficialUrls() {
    if (confirm('$id_1067')) {
        for (const [fieldId, url] of Object.entries(officialUrls)) {
            const field = document.getElementById(fieldId);
            if (field && !field.disabled) field.value = url;
        }
        showStatus('✅ $id_1068"$id_612"$id_1066', 'success');
    }
}

// =====================================================================
// $id_709
// =====================================================================
async function refreshUsageStats() {
    const loading = document.getElementById('usageLoading');
    const list = document.getElementById('usageList');

    try {
        loading.style.display = 'block';
        list.innerHTML = '';

        const [statsResponse, aggregatedResponse] = await Promise.all([
            fetch('./usage/stats', { headers: getAuthHeaders() }),
            fetch('./usage/aggregated', { headers: getAuthHeaders() })
        ]);

        if (statsResponse.status === 401 || aggregatedResponse.status === 401) {
            showStatus('$id_1069', 'error');
            setTimeout(() => location.reload(), 1500);
            return;
        }

        const statsData = await statsResponse.json();
        const aggregatedData = await aggregatedResponse.json();

        if (statsResponse.ok && aggregatedResponse.ok) {
            AppState.usageStatsData = statsData.success ? statsData.data : statsData;

            const aggData = aggregatedData.success ? aggregatedData.data : aggregatedData;
            document.getElementById('totalApiCalls').textContent = aggData.total_calls_24h || 0;
            document.getElementById('totalFiles').textContent = aggData.total_files || 0;
            document.getElementById('avgCallsPerFile').textContent = (aggData.avg_calls_per_file || 0).toFixed(1);

            renderUsageList();

            showStatus(`$id_722 ${aggData.total_files || Object.keys(AppState.usageStatsData).length} $id_1070`, 'success');
        } else {
            const errorMsg = statsData.detail || aggregatedData.detail || '$id_1071';
            showStatus(`$id_806: ${errorMsg}`, 'error');
        }
    } catch (error) {
        showStatus(`$id_729: ${error.message}`, 'error');
    } finally {
        loading.style.display = 'none';
    }
}

function renderUsageList() {
    const list = document.getElementById('usageList');
    list.innerHTML = '';

    if (Object.keys(AppState.usageStatsData).length === 0) {
        list.innerHTML = '<p style="text-align: center; color: #666;">$id_1072</p>';
        return;
    }

    for (const [filename, stats] of Object.entries(AppState.usageStatsData)) {
        const card = document.createElement('div');
        card.className = 'usage-card';

        const calls24h = stats.calls_24h || 0;

        card.innerHTML = `
            <div class="usage-header">
                <div class="usage-filename">${filename}</div>
            </div>
            <div class="usage-info">
                <div class="usage-info-item" style="grid-column: 1 / -1;">
                    <span class="usage-info-label">24$id_1073</span>
                    <span class="usage-info-value" style="font-size: 24px; font-weight: bold; color: #007bff;">${calls24h}</span>
                </div>
            </div>
            <div class="usage-actions">
                <button class="usage-btn reset" onclick="resetSingleUsageStats('${filename}')">$id_1074</button>
            </div>
        `;

        list.appendChild(card);
    }
}

async function resetSingleUsageStats(filename) {
    if (!confirm(`$id_1076 ${filename} $id_1075`)) return;

    try {
        const response = await fetch('./usage/reset', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({ filename })
        });

        const data = await response.json();

        if (response.ok && data.success) {
            showStatus(data.message, 'success');
            await refreshUsageStats();
        } else {
            showStatus(`$id_1077: ${data.message || data.detail || data.error || '$id_727'}`, 'error');
        }
    } catch (error) {
        showStatus(`$id_729: ${error.message}`, 'error');
    }
}

async function resetAllUsageStats() {
    if (!confirm('$id_1078')) return;

    try {
        const response = await fetch('./usage/reset', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({})
        });

        const data = await response.json();

        if (response.ok && data.success) {
            showStatus(data.message, 'success');
            await refreshUsageStats();
        } else {
            showStatus(`$id_1077: ${data.message || data.detail || data.error || '$id_727'}`, 'error');
        }
    } catch (error) {
        showStatus(`$id_729: ${error.message}`, 'error');
    }
}

// =====================================================================
// $id_1079
// =====================================================================
function startCooldownTimer() {
    if (AppState.cooldownTimerInterval) {
        clearInterval(AppState.cooldownTimerInterval);
    }

    AppState.cooldownTimerInterval = setInterval(() => {
        updateCooldownDisplays();
    }, 1000);
}

function stopCooldownTimer() {
    if (AppState.cooldownTimerInterval) {
        clearInterval(AppState.cooldownTimerInterval);
        AppState.cooldownTimerInterval = null;
    }
}

function updateCooldownDisplays() {
    let needsRefresh = false;

    // $id_1080
    for (const credInfo of Object.values(AppState.creds.data)) {
        if (credInfo.model_cooldowns && Object.keys(credInfo.model_cooldowns).length > 0) {
            const currentTime = Date.now() / 1000;
            const hasExpiredCooldowns = Object.entries(credInfo.model_cooldowns).some(([, until]) => until <= currentTime);

            if (hasExpiredCooldowns) {
                needsRefresh = true;
                break;
            }
        }
    }

    if (needsRefresh) {
        AppState.creds.renderList();
        return;
    }

    // $id_1081
    document.querySelectorAll('.cooldown-badge').forEach(badge => {
        const card = badge.closest('.cred-card');
        const filenameEl = card?.querySelector('.cred-filename');
        if (!filenameEl) return;

        const filename = filenameEl.textContent;
        const credInfo = Object.values(AppState.creds.data).find(c => c.filename === filename);

        if (credInfo && credInfo.model_cooldowns) {
            const currentTime = Date.now() / 1000;
            const titleMatch = badge.getAttribute('title')?.match(/$id_794: (.+)/);
            if (titleMatch) {
                const model = titleMatch[1];
                const cooldownUntil = credInfo.model_cooldowns[model];
                if (cooldownUntil) {
                    const remaining = Math.max(0, Math.floor(cooldownUntil - currentTime));
                    if (remaining > 0) {
                        const shortModel = model.replace('gemini-', '').replace('-exp', '')
                            .replace('2.0-', '2-').replace('1.5-', '1.5-');
                        const timeDisplay = formatCooldownTime(remaining).replace(/s$/, '').replace(/ /g, '');
                        badge.innerHTML = `🔧 ${shortModel}: ${timeDisplay}`;
                    }
                }
            }
        }
    });
}

// =====================================================================
// $id_1082
// =====================================================================

// $id_1083
async function fetchAndDisplayVersion() {
    try {
        const response = await fetch('./version/info');
        const data = await response.json();

        const versionText = document.getElementById('versionText');

        if (data.success) {
            // $id_1084
            versionText.textContent = `v${data.version}`;
            versionText.title = `$id_1085: ${data.full_hash}\n$id_1086: ${data.message}\n$id_1087: ${data.date}`;
            versionText.style.cursor = 'help';
        } else {
            versionText.textContent = '$id_1088';
            versionText.title = data.error || '$id_1089';
        }
    } catch (error) {
        console.error('$id_1090:', error);
        const versionText = document.getElementById('versionText');
        if (versionText) {
            versionText.textContent = '$id_1091';
        }
    }
}

// $id_1092
async function checkForUpdates() {
    const checkBtn = document.getElementById('checkUpdateBtn');
    if (!checkBtn) return;

    const originalText = checkBtn.textContent;

    try {
        // $id_1093
        checkBtn.textContent = '$id_1094...';
        checkBtn.disabled = true;

        // $id_1095API$id_1092
        const response = await fetch('./version/info?check_update=true');
        const data = await response.json();

        if (data.success) {
            if (data.check_update === false) {
                // $id_1096
                showStatus(`$id_1096: ${data.update_error || '$id_727'}`, 'error');
            } else if (data.has_update === true) {
                // $id_1097
                const updateMsg = `$id_1098\n$id_392: v${data.version}\n$id_1100: v${data.latest_version}\n\n$id_1099: ${data.latest_message || '$id_39'}`;
                showStatus(updateMsg.replace(/\n/g, ' '), 'warning');

                // $id_1101
                checkBtn.style.backgroundColor = '#ffc107';
                checkBtn.textContent = '$id_1102';

                setTimeout(() => {
                    checkBtn.style.backgroundColor = '#17a2b8';
                    checkBtn.textContent = originalText;
                }, 5000);
            } else if (data.has_update === false) {
                // $id_1103
                showStatus('$id_1104', 'success');

                checkBtn.style.backgroundColor = '#28a745';
                checkBtn.textContent = '$id_1103';

                setTimeout(() => {
                    checkBtn.style.backgroundColor = '#17a2b8';
                    checkBtn.textContent = originalText;
                }, 3000);
            } else {
                // $id_1105
                showStatus('$id_1106', 'info');
            }
        } else {
            showStatus(`$id_1096: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('$id_1096:', error);
        showStatus(`$id_1096: ${error.message}`, 'error');
    } finally {
        checkBtn.disabled = false;
        if (checkBtn.textContent === '$id_1094...') {
            checkBtn.textContent = originalText;
        }
    }
}

// =====================================================================
// $id_1107
// =====================================================================
window.onload = async function () {
    const autoLoginSuccess = await autoLogin();

    if (!autoLoginSuccess) {
        showStatus('$id_1108', 'info');
    } else {
        // $id_1109
        await fetchAndDisplayVersion();
    }

    startCooldownTimer();

    const antigravityAuthBtn = document.getElementById('getAntigravityAuthBtn');
    if (antigravityAuthBtn) {
        antigravityAuthBtn.addEventListener('click', startAntigravityAuth);
    }
};

// $id_1110 - $id_1111
document.addEventListener('DOMContentLoaded', function () {
    const uploadArea = document.getElementById('uploadArea');

    if (uploadArea) {
        uploadArea.addEventListener('dragover', (event) => {
            event.preventDefault();
            uploadArea.classList.add('dragover');
        });

        uploadArea.addEventListener('dragleave', (event) => {
            event.preventDefault();
            uploadArea.classList.remove('dragover');
        });

        uploadArea.addEventListener('drop', (event) => {
            event.preventDefault();
            uploadArea.classList.remove('dragover');
            AppState.uploadFiles.addFiles(Array.from(event.dataTransfer.files));
        });
    }
});
