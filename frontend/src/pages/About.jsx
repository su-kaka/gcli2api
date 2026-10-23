import { ExternalLink, Github, Info, RefreshCw, Rocket, Zap } from "lucide-react";

export default function About({ version, onCheckUpdate, checkingUpdate }) {
  const apiEndpoints = [
    { label: "OpenAI 格式", value: `${window.location.origin}/v1/chat/completions` },
    { label: "Gemini 格式", value: `${window.location.origin}/v1beta/models` },
    { label: "Claude 格式", value: `${window.location.origin}/v1/messages` },
  ];

  const copyText = (text) => {
    navigator.clipboard.writeText(text);
  };

  return (
    <div className="space-y-6">
      {/* API 端点 */}
      <div className="card p-4 sm:p-6">
        <h3 className="text-sm font-medium text-gray-400 mb-3 flex items-center gap-2">
          <Zap size={16} className="text-amber-400" />
          API 端点（点击复制）
        </h3>
        <div className="space-y-2">
          {apiEndpoints.map((ep) => (
            <div key={ep.label} className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-3 text-sm">
              <span className="text-gray-500 sm:w-24 flex-shrink-0">{ep.label}</span>
              <code
                className="bg-dark-800 border border-dark-600 rounded px-2 py-1 text-xs text-gray-300 cursor-pointer hover:text-white break-all"
                onClick={() => copyText(ep.value)}
              >
                {ep.value}
              </code>
            </div>
          ))}
        </div>
      </div>

      {/* 项目简介 */}
      <div className="card">
        <div className="flex items-center gap-3 mb-4">
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-2xl bg-purple-600/20">
            <Rocket className="w-7 h-7 text-purple-400" />
          </div>
          <div>
            <h2 className="text-2xl font-bold text-white">AGY2API</h2>
            <p className="text-gray-400 text-sm">
              {version?.version ? `版本: ${version.version}` : "Antigravity API 代理服务"}
            </p>
          </div>
        </div>
        <p className="text-gray-400 text-sm leading-relaxed">
          将 Google Antigravity API 转换为 OpenAI、Gemini 和 Anthropic
          兼容格式的代理工具。支持多账户管理（邮箱去重）、自动轮换、模型级冷却、凭证导入导出等功能。
        </p>
        <div className="flex flex-wrap items-center gap-4 mt-4">
          <a
            href="https://github.com/su-kaka/agy2api"
            target="_blank"
            rel="noopener noreferrer"
            className="text-purple-400 hover:text-purple-300 text-sm flex items-center gap-1.5"
          >
            <Github size={15} />
            GitHub 仓库
            <ExternalLink size={12} />
          </a>
          <button
            onClick={onCheckUpdate}
            disabled={checkingUpdate}
            className="text-blue-400 hover:text-blue-300 text-sm flex items-center gap-1.5 disabled:opacity-50"
          >
            {checkingUpdate ? (
              <Info size={14} className="animate-pulse" />
            ) : (
              <RefreshCw size={14} />
            )}
            {checkingUpdate ? "检查中..." : "检查更新"}
          </button>
          {version?.check_update && version?.has_update && (
            <span className="text-emerald-400 text-sm flex items-center gap-1.5">
              <Info size={14} />
              有新版本可用: {version.latest_version}
            </span>
          )}
          {version?.check_update && version?.has_update === false && (
            <span className="text-gray-400 text-sm flex items-center gap-1.5">
              <Info size={14} />
              已是最新版本
            </span>
          )}
        </div>
      </div>

      {/* 主要功能 */}
      <div className="card">
        <h3 className="text-lg font-semibold text-blue-400 mb-4">✨ 主要功能</h3>
        <div className="grid md:grid-cols-2 gap-4 text-sm">
          <div className="space-y-3">
            <div className="text-gray-300">
              <span className="text-amber-400">⚡</span>{" "}
              <strong>多格式兼容：</strong>OpenAI / Gemini / Anthropic 三种 API 格式
            </div>
            <div className="text-gray-300">
              <span className="text-cyan-400">🔄</span>{" "}
              <strong>自动轮换：</strong>随机负载均衡，自动跳过冷却和禁用的凭证
            </div>
            <div className="text-gray-300">
              <span className="text-emerald-400">🛡️</span>{" "}
              <strong>自动封禁：</strong>遇到指定错误码自动禁用异常凭证
            </div>
            <div className="text-gray-300">
              <span className="text-purple-400">❄️</span>{" "}
              <strong>模型级冷却：</strong>429 错误自动设置模型级 CD
            </div>
          </div>
          <div className="space-y-3">
            <div className="text-gray-300">
              <span className="text-red-400">✂️</span>{" "}
              <strong>抗截断：</strong>"抗截断-" 前缀模型自动续传截断的响应
            </div>
            <div className="text-gray-300">
              <span className="text-emerald-400">📊</span>{" "}
              <strong>实时监控：</strong>凭证状态、错误码、额度查询
            </div>
            <div className="text-gray-300">
              <span className="text-gray-400">📱</span>{" "}
              <strong>响应式界面：</strong>桌面/移动端自适应
            </div>
          </div>
        </div>
      </div>

      {/* 交流群 */}
      <div className="card text-center">
        <h3 className="text-lg font-semibold text-blue-400 mb-4">💬 交流群</h3>
        <p className="text-gray-400 text-sm mb-3">欢迎加入 QQ 群交流讨论！</p>
        <div className="inline-block bg-white rounded-xl p-3">
          <img
            src="/docs/qq-group.jpg"
            alt="QQ 群二维码"
            className="w-40 h-40 rounded-lg"
          />
        </div>
        <p className="text-blue-400 font-semibold mt-3">QQ 群号：1083250744</p>
      </div>

      {/* 使用声明 */}
      <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-5">
        <div className="text-amber-400 font-medium mb-2">⚠️ 使用声明</div>
        <p className="text-sm text-amber-400/80">
          本项目仅供学习研究使用，禁止商业用途和倒卖。
        </p>
      </div>

      {/* 版本信息 */}
      {version && (
        <div className="card">
          <h3 className="text-base font-semibold text-gray-300 mb-3">版本信息</h3>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div className="text-gray-500">
              当前版本: <span className="text-gray-300">{version.version || "-"}</span>
            </div>
            <div className="text-gray-500">
              构建哈希: <span className="text-gray-300">{version.full_hash || "-"}</span>
            </div>
            <div className="text-gray-500">
              更新说明: <span className="text-gray-300">{version.message || "-"}</span>
            </div>
            <div className="text-gray-500">
              构建日期: <span className="text-gray-300">{version.date || "-"}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
