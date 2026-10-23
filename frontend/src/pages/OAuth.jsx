import { Check, ChevronDown, Copy, ExternalLink, Loader2, Rocket } from "lucide-react";
import { useState } from "react";
import api from "../api";

export default function OAuth() {
  const [loading, setLoading] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [authUrl, setAuthUrl] = useState("");
  const [callbackUrl, setCallbackUrl] = useState("");
  const [showCallbackInput, setShowCallbackInput] = useState(false);
  const [credsContent, setCredsContent] = useState("");
  const [message, setMessage] = useState(null);
  const [copied, setCopied] = useState(false);

  const startAuth = async () => {
    setLoading(true);
    setMessage(null);
    setAuthUrl("");
    setCredsContent("");

    try {
      const res = await api.post("/auth/start", {});
      setAuthUrl(res.data.auth_url);
      setMessage({ type: "info", text: "已生成认证链接，请在新窗口完成 Google 授权" });
    } catch (err) {
      setMessage({
        type: "error",
        text: err.response?.data?.detail || "获取认证链接失败",
      });
    } finally {
      setLoading(false);
    }
  };

  const fetchCredentials = async () => {
    setFetching(true);
    try {
      const res = await api.post("/auth/callback", {});
      setCredsContent(JSON.stringify(res.data.credentials, null, 2));
      setMessage({
        type: "success",
        text: `认证成功！凭证已保存：${res.data.email || "未知邮箱"}`,
      });
    } catch (err) {
      setMessage({
        type: "error",
        text: err.response?.data?.detail || "获取凭证失败，请确认已完成授权",
      });
    } finally {
      setFetching(false);
    }
  };

  const processCallbackUrl = async () => {
    if (!callbackUrl.trim()) {
      setMessage({ type: "error", text: "请粘贴完整的回调URL" });
      return;
    }
    setFetching(true);
    try {
      const res = await api.post("/auth/callback-url", {
        callback_url: callbackUrl.trim(),
      });
      setCredsContent(JSON.stringify(res.data.credentials, null, 2));
      setMessage({
        type: "success",
        text: `认证成功！凭证已保存：${res.data.email || "未知邮箱"}`,
      });
    } catch (err) {
      setMessage({
        type: "error",
        text: err.response?.data?.detail || "从回调URL获取凭证失败",
      });
    } finally {
      setFetching(false);
    }
  };

  const copyUrl = () => {
    navigator.clipboard.writeText(authUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const downloadCreds = () => {
    const blob = new Blob([credsContent], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "antigravity-credentials.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6">
      {/* 说明卡片 */}
      <div className="bg-gradient-to-br from-orange-900/30 to-orange-800/10 border border-orange-700/30 rounded-xl p-5">
        <div className="flex items-center gap-2 mb-2">
          <Rocket className="w-5 h-5 text-orange-400" />
          <h3 className="text-lg font-semibold text-orange-400">Antigravity 认证模式</h3>
        </div>
        <p className="text-sm text-gray-400">
          通过 Google OAuth 获取 Antigravity 凭证。授权成功后系统会自动获取 Project ID
          和订阅等级，并保存凭证到数据库。
        </p>
      </div>

      {/* 消息提示 */}
      {message && (
        <div
          className={`p-4 rounded-xl border ${
            message.type === "success"
              ? "bg-green-500/10 border-green-500/30 text-green-400"
              : message.type === "error"
              ? "bg-red-500/10 border-red-500/30 text-red-400"
              : "bg-blue-500/10 border-blue-500/30 text-blue-400"
          }`}
        >
          <div className="flex items-center justify-between">
            <span>{message.text}</span>
            <button
              onClick={() => setMessage(null)}
              className="text-gray-400 hover:text-white"
            >
              ✕
            </button>
          </div>
        </div>
      )}

      {/* 获取认证链接按钮 */}
      <button
        onClick={startAuth}
        disabled={loading}
        className="btn btn-primary flex items-center gap-2 disabled:opacity-50"
      >
        {loading ? (
          <Loader2 className="animate-spin" size={18} />
        ) : (
          <Rocket size={18} />
        )}
        获取 Antigravity 认证链接
      </button>

      {/* 认证链接 */}
      {authUrl && (
        <div className="card space-y-4">
          <h3 className="font-semibold">认证链接</h3>
          <div className="bg-dark-800 border border-purple-500/30 rounded-lg p-4 flex items-center justify-between gap-3">
            <a
              href={authUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-purple-400 hover:text-purple-300 break-all text-sm flex items-center gap-1"
            >
              点击此链接进行 Google 认证 <ExternalLink size={14} />
            </a>
            <button
              onClick={copyUrl}
              className="btn btn-secondary text-xs px-3 py-1.5 flex items-center gap-1 flex-shrink-0"
            >
              {copied ? <Check size={14} /> : <Copy size={14} />}
              {copied ? "已复制" : "复制"}
            </button>
          </div>

          <div className="bg-blue-500/10 border border-blue-500/30 rounded-lg p-4 text-sm text-gray-400">
            <p className="font-medium text-blue-400 mb-2">使用说明</p>
            <ol className="list-decimal list-inside space-y-1">
              <li>点击上方认证链接，在新窗口中完成 Google 账号授权</li>
              <li>授权成功后会跳转到 localhost 显示成功页面</li>
              <li>关闭 OAuth 窗口，返回本页面</li>
              <li>点击下方"获取凭证"按钮完成流程</li>
            </ol>
          </div>

          {/* 快捷回调URL */}
          <div className="border border-dark-600 rounded-lg">
            <button
              onClick={() => setShowCallbackInput(!showCallbackInput)}
              className="w-full flex items-center justify-between px-4 py-3 text-sm text-gray-400 hover:text-gray-200"
            >
              <span>🚀 无法回源？试试快捷方式（粘贴回调URL）</span>
              <ChevronDown
                size={16}
                className={`transition-transform ${showCallbackInput ? "rotate-180" : ""}`}
              />
            </button>
            {showCallbackInput && (
              <div className="px-4 pb-4 space-y-3">
                <p className="text-xs text-gray-500">
                  适用场景：云服务器/VPS、Docker 容器、防火墙阻止回调端口等。
                  完成 Google 授权后，复制浏览器地址栏的完整 URL 粘贴到下方。
                </p>
                <textarea
                  value={callbackUrl}
                  onChange={(e) => setCallbackUrl(e.target.value)}
                  placeholder="http://localhost:11451/?state=xxx&code=xxx&scope=xxx..."
                  className="w-full px-3 py-2 bg-dark-800 border border-dark-600 rounded-lg text-sm text-white placeholder-gray-500"
                  rows={3}
                />
                <button
                  onClick={processCallbackUrl}
                  disabled={fetching}
                  className="btn btn-secondary text-sm flex items-center gap-2 disabled:opacity-50"
                >
                  {fetching && <Loader2 className="animate-spin" size={14} />}
                  从回调URL获取凭证
                </button>
              </div>
            )}
          </div>

          {/* 获取凭证按钮 */}
          <button
            onClick={fetchCredentials}
            disabled={fetching}
            className="btn btn-primary flex items-center gap-2 disabled:opacity-50"
          >
            {fetching && <Loader2 className="animate-spin" size={18} />}
            获取 Antigravity 凭证
          </button>
        </div>
      )}

      {/* 凭证内容 */}
      {credsContent && (
        <div className="card space-y-4">
          <h3 className="font-semibold">凭证内容</h3>
          <pre className="bg-dark-800 border border-dark-600 rounded-lg p-4 text-xs text-gray-300 overflow-x-auto max-h-96 overflow-y-auto">
            {credsContent}
          </pre>
          <button onClick={downloadCreds} className="btn btn-secondary text-sm">
            下载凭证文件
          </button>
        </div>
      )}
    </div>
  );
}
