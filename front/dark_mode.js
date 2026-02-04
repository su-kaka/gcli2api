// =====================================================================
// 夜间模式管理模块
// =====================================================================

/**
 * 切换夜间模式开关状态
 */
function toggleDarkMode() {
    document.body.classList.toggle('dark-mode');
    const isDark = document.body.classList.contains('dark-mode');
    localStorage.setItem('gcli2api_dark_mode', isDark);
    updateDarkModeBtn(isDark);
}

/**
 * 初始化夜间模式
 * 根据本地存储或系统偏好设置应用模式
 */
function initDarkMode() {
    const saved = localStorage.getItem('gcli2api_dark_mode');
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    
    // 如果有保存的设置则使用保存的，否则跟随系统
    const shouldBeDark = saved === 'true' || (saved === null && prefersDark);
    
    if (shouldBeDark) {
        document.body.classList.add('dark-mode');
    } else {
        document.body.classList.remove('dark-mode');
    }
    updateDarkModeBtn(shouldBeDark);
}

/**
 * 更新所有夜间模式切换按钮的状态（图标和提示）
 * @param {boolean} isDark - 当前是否为夜间模式
 */
function updateDarkModeBtn(isDark) {
    const btns = document.querySelectorAll('.dark-mode-toggle');
    btns.forEach(btn => {
        // 使用 emoji 作为图标
        btn.innerHTML = isDark ? '☀️' : '🌙';
        btn.title = isDark ? '切换亮色模式' : '切换夜间模式';
        
        // 添加旋转动画效果
        btn.style.transition = 'transform 0.3s ease';
        btn.style.transform = 'rotate(360deg)';
        setTimeout(() => btn.style.transform = 'rotate(0deg)', 300);
    });
}

// 监听 DOM 加载完成事件进行初始化
document.addEventListener('DOMContentLoaded', () => {
    initDarkMode();
});
