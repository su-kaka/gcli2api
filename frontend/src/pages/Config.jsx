import { Loader2, RefreshCw, Save } from "lucide-react";
import { useEffect, useState } from "react";
import api from "../api";

const CONFIG_GROUPS = [
  {
    title: "服务器配置",
    fields: [
      { key: "host", label: "服务器主机地址", type: "text", note: "服务器监听的主机地址，0.0.0.0表示监听所有接口（重启生效）" },
      { key: "port", label: "服务器端口", type: "number", note: "服务器监听的端口号（重启生效）" },
      { key: "api_password", label: "API 访问密码", type: "text", note: "聊天API访问密码，用于 OpenAI / Gemini / Claude 端点认证" },
      { key: "panel_password", label: "控制面板密码", type: "text", note: "控制面板访问密码" },
    ],
  },
  {
    title: "基础配置",
    fields: [
      { key: "credentials_dir", label: "凭证目录路径", type: "text", note: "存储认证文件的目录路径" },
      { key: "proxy", label: "代理设置", type: "text", note: "HTTP/HTTPS/SOCKS5 代理，留空不使用（重启生效）", placeholder: "http://proxy:port 或 socks5://proxy:port" },
    ],
  },
  {
    title: "端点配置",
    fields: [
      { key: "oauth_proxy_url", label: "OAuth Endpoint", type: "text", note: "Google OAuth2 端点，用于 token 获取和刷新" },
      { key: "googleapis_proxy_url", label: "Google APIs Endpoint", type: "text", note: "Google APIs 端点" },
      { key: "resource_manager_api_url", label: "Resource Manager API", type: "text", note: "Google Cloud Resource Manager API 端点" },
      { key: "service_usage_api_url", label: "Service Usage API", type: "text", note: "Google Cloud Service Usage API 端点" },
      { key: "antigravity_api_url", label: "Antigravity API Endpoint", type: "text", note: "Antigravity API 端点" },
    ],
  },
  {
    title: "自动封禁配置",
    fields: [
      { key: "auto_ban_enabled", label: "启用自动封禁", type: "bool", note: "遇到指定错误码时自动禁用凭证" },
      { key: "auto_ban_error_codes", label: "自动封禁错误码", type: "text", note: "JSON 数组格式，如 [403, 400]" },
    ],
  },
  {
    title: "重试配置",
    fields: [
      { key: "retry_429_enabled", label: "启用错误重试", type: "bool", note: "遇到错误时自动重试" },
      { key: "retry_429_max_retries", label: "最大重试次数", type: "number", note: "遇到错误时的最大重试次数" },
      { key: "retry_429_interval", label: "重试间隔（秒）", type: "number", note: "两次重试间的等待秒数，允许 0（立即重试），范围 0-3600" },
    ],
  },
  {
    title: "兼容性配置",
    fields: [
      { key: "return_thoughts_to_frontend", label: "返回思维链到前端", type: "bool", note: "启用后思维链会在响应中返回；禁用后过滤掉" },
      { key: "antigravity_switch_credential_enabled", label: "Antigravity 切换凭证", type: "bool", note: "启用后重试时切换凭证；禁用时保持当前凭证直到 CD 或被禁用" },
    ],
  },
  {
    title: "抗截断配置",
    fields: [
      { key: "anti_truncation_max_attempts", label: "抗截断最大重试次数", type: "number", note: "检测到输出截断时的最大续传尝试次数（1-10）。使用'抗截断/'前缀的模型生效" },
    ],
  },
];

export default function Config() {
  const [config, setConfig] = useState({});
  const [envLocked, setEnvLocked] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null);

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    setLoading(true);
    try {
      const res = await api.get("/config/get");
      setConfig(res.data.config || {});
      setEnvLocked(res.data.env_locked || []);
      setMessage(null);
    } catch (err) {
      setMessage({ type: "error", text: err.response?.data?.detail || "加载配置失败" });
    } finally {
      setLoading(false);
    }
  };

  const saveConfig = async () => {
    setSaving(true);
    try {
      const res = await api.post("/config/save", { config });
      setMessage({ type: "success", text: res.data.message || "配置保存成功" });
      loadConfig();
    } catch (err) {
      setMessage({ type: "error", text: err.response?.data?.detail || "保存配置失败" });
    } finally {
      setSaving(false);
    }
  };

  const setField = (key, value) => {
    setConfig((prev) => ({ ...prev, [key]: value }));
  };

  // 一键镜像/官方端点（墙内访问 Google 官方端点的反代方案）
  const MIRROR_BASE = "https://gcli-api.sukaka.top";
  const OFFICIAL_ENDPOINTS = {
    oauth_proxy_url: "https://oauth2.googleapis.com",
    googleapis_proxy_url: "https://www.googleapis.com",
    resource_manager_api_url: "https://cloudresourcemanager.googleapis.com",
    service_usage_api_url: "https://serviceusage.googleapis.com",
    antigravity_api_url: "https://daily-cloudcode-pa.googleapis.com",
  };
  const MIRROR_PATHS = {
    oauth_proxy_url: "/oauth2",
    googleapis_proxy_url: "/googleapis",
    resource_manager_api_url: "/cloudresourcemanager",
    service_usage_api_url: "/serviceusage",
    antigravity_api_url: "/daily-cloudcode-pa",
  };

  const useMirrorUrls = () => {
    setConfig((prev) => {
      const next = { ...prev };
      for (const [key, path] of Object.entries(MIRROR_PATHS)) {
        if (!envLocked.includes(key)) next[key] = MIRROR_BASE + path;
      }
      return next;
    });
  };

  const restoreOfficialUrls = () => {
    setConfig((prev) => {
      const next = { ...prev };
      for (const [key, url] of Object.entries(OFFICIAL_ENDPOINTS)) {
        if (!envLocked.includes(key)) next[key] = url;
      }
      return next;
    });
  };

  const renderField = (field) => {
    const isLocked = envLocked.includes(field.key);
    const value = config[field.key] ?? "";

    if (isLocked) {
      return (
        <div className="text-xs text-amber-400/80 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
          该配置已被环境变量锁定，修改环境变量后重启生效
        </div>
      );
    }

    if (field.type === "bool") {
      return (
        <label className="flex items-center gap-3 cursor-pointer select-none">
          <button
            type="button"
            onClick={() => setField(field.key, !value)}
            className={`relative w-11 h-6 rounded-full transition-colors ${
              value ? "bg-purple-600" : "bg-dark-600"
            }`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
                value ? "translate-x-5" : ""
              }`}
            />
          </button>
          <span className="text-sm text-gray-300">{value ? "已启用" : "已禁用"}</span>
        </label>
      );
    }

    return (
      <input
        type={field.type}
        value={value}
        onChange={(e) =>
          setField(
            field.key,
            field.type === "number" ? Number(e.target.value) : e.target.value
          )
        }
        placeholder={field.placeholder}
        className="w-full px-3 py-2 bg-dark-800 border border-dark-600 rounded-lg text-sm text-white placeholder-gray-500"
      />
    );
  };

  return (
    <div className="space-y-6">
      {/* 操作按钮 */}
      <div className="flex items-center gap-2">
        <button
          onClick={loadConfig}
          disabled={loading}
          className="btn btn-secondary text-sm flex items-center gap-1.5"
        >
          <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
          刷新配置
        </button>
        <button
          onClick={saveConfig}
          disabled={saving || loading}
          className="btn btn-primary text-sm flex items-center gap-1.5 disabled:opacity-50"
        >
          {saving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
          {saving ? "保存中..." : "保存配置"}
        </button>
      </div>

      {/* 消息 */}
      {message && (
        <div
          className={`p-4 rounded-xl border ${
            message.type === "success"
              ? "bg-green-500/10 border-green-500/30 text-green-400"
              : "bg-red-500/10 border-red-500/30 text-red-400"
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

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="animate-spin text-purple-500" size={28} />
        </div>
      ) : (
        <div className="space-y-6">
          {CONFIG_GROUPS.map((group) => (
            <div key={group.title} className="card">
              <h3 className="text-base font-semibold text-purple-400 mb-4 border-b border-dark-700 pb-3">
                {group.title}
              </h3>

              {group.title === "端点配置" && (
                <div className="flex flex-wrap gap-2 mb-4">
                  <button
                    type="button"
                    onClick={useMirrorUrls}
                    className="btn btn-secondary text-xs px-3 py-1.5"
                  >
                    🚀 一键使用镜像网址
                  </button>
                  <button
                    type="button"
                    onClick={restoreOfficialUrls}
                    className="btn btn-secondary text-xs px-3 py-1.5"
                  >
                    🔄 还原官方端点
                  </button>
                </div>
              )}

              <div className="space-y-5">
                {group.fields.map((field) => (
                  <div key={field.key}>
                    <label className="block text-sm font-medium text-gray-300 mb-1.5">
                      {field.label}
                    </label>
                    {renderField(field)}
                    {field.note && (
                      <p className="text-xs text-gray-500 mt-1.5">{field.note}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
