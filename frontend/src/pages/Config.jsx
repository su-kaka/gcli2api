import { Loader2, Plus, RefreshCw, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import Modal from "../components/Modal";
import Select from "../components/Select";
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
    title: "IP 白名单",
    fields: [
      { key: "ip_whitelist_enabled", label: "启用 IP 白名单", type: "bool", note: "开启后仅白名单内的 IP 可访问面板和 API，其余 IP 直接 403。留空列表或关闭开关则不限制。保存后立即生效（热更新）" },
      { key: "ip_whitelist", label: "IP 白名单列表", type: "textarea", showIf: (cfg) => !!cfg.ip_whitelist_enabled, note: "每行一条，支持单个 IP、CIDR 网段、起止范围（- 或 ~ 分隔），IPv4 / IPv6 均可", placeholder: "例如：\n127.0.0.1\n192.168.1.0/24\n10.0.0.1-10.0.0.100\n::1\n2001:db8::/32" },
    ],
  },
  {
    title: "基础配置",
    fields: [
      { key: "credentials_dir", label: "凭证目录路径", type: "text", note: "存储认证文件的目录路径" },
      { key: "proxy", label: "代理设置", type: "text", note: "支持 http/https/socks5/socks5h，可带账号密码（user:pass@host:port），留空不使用（重启生效）", placeholder: "http://user:pass@proxy:port 或 socks5h://proxy:port" },
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
    title: "凭证选择模式",
    fields: [
      {
        key: "credential_select_mode",
        label: "选择模式",
        type: "select",
        note: "决定每次 API 请求如何挑选凭证",
        options: [
          { value: "random", label: "完全随机" },
          { value: "cache", label: "缓存匹配（推荐）" },
          { value: "sticky", label: "凭证不变直到 CD" },
        ],
        descriptions: {
          random: "完全随机负载均衡：每次请求从可用凭证中随机挑选一个，最均匀地分摊负载，但相同会话可能命中不同凭证，无法利用上游 prompt cache。",
          cache: "缓存匹配（默认推荐）：以\"模型 + system 指令（或首条 user 消息）\"哈希出会话 key，相同会话始终复用同一凭证——命中 Google 上游的 prompt cache，长对话更快、更省 token。凭证进入 CD 后自动切换并重新绑定。建议搭配 Redis（REDIS_URL）实现多实例共享。",
          sticky: "凭证粘性：所有请求锁定同一个凭证，直到该凭证进入冷却（CD）或被禁用才切换到下一个。适合最大化单凭证的缓存命中，但负载不均——单凭证扛满全量请求，其余凭证闲置。",
        },
      },
    ],
  },
  {
    title: "响应配置",
    fields: [
      { key: "return_thoughts_to_frontend", label: "返回思维链到前端", type: "bool", note: "启用后思维链会在响应中返回；禁用后过滤掉" },
    ],
  },
  {
    title: "抗截断配置",
    fields: [
      { key: "anti_truncation_max_attempts", label: "抗截断最大重试次数", type: "number", note: "检测到输出截断时的最大续传尝试次数（1-10）。使用'抗截断-'前缀的模型生效" },
    ],
  },
  {
    title: "敏感词过滤",
    fields: [
      { key: "sensitive_words_enabled", label: "启用敏感词过滤", type: "bool", note: "请求中命中敏感词的内容会被替换为空，避免触发 Google API 端点的敏感词拦截" },
      { key: "sensitive_words", label: "敏感词列表", type: "textarea", showIf: (cfg) => !!cfg.sensitive_words_enabled, note: "每行一个词或短语，不区分大小写，支持中文。命中后整词替换为空。保存后立即生效（热更新）", placeholder: "例如：\nsensitive word\n敏感词\n另一个词" },
    ],
  },
  {
    title: "模型名复写",
    fields: [
      { key: "model_rewrite_enabled", label: "启用模型名复写", type: "bool", note: "开启后按规则匹配请求中的模型名（支持正则），命中则替换为规则指定的模型名。常用于把客户端固定模型名映射到实际可用的上游模型。保存后立即生效（热更新）" },
      {
        key: "model_rewrite_rules",
        label: "复写规则",
        type: "rules",
        showIf: (cfg) => !!cfg.model_rewrite_enabled,
        note: "每行一条规则：匹配正则 + 替换模型名。自上而下首条命中即生效，替换支持正则反向引用（如 \\1）",
      },
    ],
  },
  {
    title: "请求日志",
    fields: [
      { key: "usage_log_enabled", label: "启用请求日志", type: "bool", note: "记录每次 API 请求（模型、凭证、token 用量、耗时、状态）到数据库，在\"请求日志\"页面查看。日志不自动清理，可在该页面手动清理或清空" },
      { key: "request_log_enabled", label: "启用请求体详细日志", type: "bool", note: "开启后，当请求命中下方错误码时，把发往上游的完整请求头（已脱敏）与请求体保存为 JSON 文件（data/request_logs/）。主要用于请求体错误时排查 debug；清理/清空日志时会同步删除这些文件" },
      { key: "request_log_error_codes", label: "详细日志错误码", type: "csv_numbers", showIf: (cfg) => !!cfg.request_log_enabled, note: "仅当最终响应状态码命中此列表时记录详细日志，逗号分隔，如 400, 429。留空默认只记录 400", placeholder: "400, 429" },
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

  const loadConfig = async ({ silent = false } = {}) => {
    setLoading(true);
    try {
      const res = await api.get("/config/get");
      setConfig(res.data.config || {});
      setEnvLocked(res.data.env_locked || []);
      if (!silent) setMessage(null);
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
      // 静默刷新配置（不清空刚显示的弹窗消息）
      await loadConfig({ silent: true });
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

    if (field.type === "select") {
      const current = field.options.find((o) => o.value === value) || field.options[0];
      return (
        <div className="space-y-2">
          <Select
            value={current?.value ?? ""}
            onChange={(v) => setField(field.key, v)}
            options={field.options}
            className="w-full sm:w-64"
          />
          {field.descriptions && current && (
            <div className="bg-dark-800/60 border border-dark-600 rounded-lg p-3 text-xs sm:text-sm text-gray-300 leading-relaxed">
              {field.descriptions[current.value]}
            </div>
          )}
        </div>
      );
    }

    if (field.type === "csv_numbers") {
      const text = Array.isArray(value) ? value.join(", ") : value || "";
      return (
        <input
          type="text"
          value={text}
          onChange={(e) => {
            const nums = e.target.value
              .split(/[,\s]+/)
              .map((x) => parseInt(x, 10))
              .filter((n) => Number.isInteger(n) && n >= 100 && n <= 599);
            setField(field.key, [...new Set(nums)]);
          }}
          placeholder={field.placeholder}
          className="w-full sm:w-64 px-3 py-2 bg-dark-800 border border-dark-600 rounded-lg text-sm text-white placeholder-gray-500 font-mono"
        />
      );
    }

    if (field.type === "rules") {
      const rules = Array.isArray(value)
        ? value.map((r) => (Array.isArray(r) ? [String(r[0] ?? ""), String(r[1] ?? "")] : ["", ""]))
        : [];
      const updateRule = (idx, col, val) => {
        const next = rules.map((r) => [...r]);
        next[idx][col] = val;
        setField(field.key, next);
      };
      const removeRule = (idx) => {
        setField(field.key, rules.filter((_, i) => i !== idx));
      };
      const addRule = () => {
        setField(field.key, [...rules, ["", ""]]);
      };
      return (
        <div className="space-y-2">
          {rules.length === 0 && (
            <div className="text-xs text-gray-500 bg-dark-800/60 border border-dark-700 rounded-lg px-3 py-2">
              暂无规则，点击下方按钮新增
            </div>
          )}
          {rules.map((rule, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <input
                type="text"
                value={rule[0]}
                onChange={(e) => updateRule(idx, 0, e.target.value)}
                placeholder="匹配正则，如 ^gpt-4$"
                className="flex-1 min-w-0 px-3 py-2 bg-dark-800 border border-dark-600 rounded-lg text-sm text-white placeholder-gray-500 font-mono"
              />
              <span className="text-gray-500 text-sm shrink-0">→</span>
              <input
                type="text"
                value={rule[1]}
                onChange={(e) => updateRule(idx, 1, e.target.value)}
                placeholder="替换模型名，如 gemini-2.5-pro"
                className="flex-1 min-w-0 px-3 py-2 bg-dark-800 border border-dark-600 rounded-lg text-sm text-white placeholder-gray-500 font-mono"
              />
              <button
                type="button"
                onClick={() => removeRule(idx)}
                className="shrink-0 p-2 text-gray-500 hover:text-red-400 transition-colors"
                title="删除该规则"
              >
                <Trash2 size={16} />
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={addRule}
            className="btn btn-secondary text-xs px-3 py-1.5 flex items-center gap-1.5"
          >
            <Plus size={14} />
            新增规则
          </button>
        </div>
      );
    }

    if (field.type === "textarea") {
      return (
        <textarea
          value={Array.isArray(value) ? value.join("\n") : value || ""}
          onChange={(e) => {
            const lines = e.target.value === "" ? [] : e.target.value.split("\n");
            setField(field.key, lines.map((w) => w.replace(/\s+$/, "")).filter((w) => w.trim() !== ""))
          }}
          placeholder={field.placeholder}
          rows={6}
          className="w-full px-3 py-2 bg-dark-800 border border-dark-600 rounded-lg text-sm text-white placeholder-gray-500 font-mono"
        />
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
      {/* 操作按钮（sticky 常驻视口顶部） */}
      <div className="sticky top-0 z-30 -mx-1 px-1 py-2 bg-dark-950/95 backdrop-blur border-b border-dark-700 rounded-lg">
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
      </div>

      {/* 消息弹窗 */}
      {message && (
        <Modal type={message.type} title={message.type === "success" ? "操作成功" : "操作失败"} onClose={() => setMessage(null)}>
          <span className="whitespace-pre-wrap break-all">{message.text}</span>
        </Modal>
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
                {group.fields
                  .filter((field) => !field.showIf || field.showIf(config))
                  .map((field) => (
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
