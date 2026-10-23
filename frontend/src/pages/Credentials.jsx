import {
  AlertCircle,
  Ban,
  BarChart3,
  CheckCircle,
  ChevronDown,
  ChevronUp,
  Clock,
  Download,
  Loader2,
  RefreshCw,
  Shield,
  Snowflake,
  Trash2,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import Modal from "../components/Modal";
import Select from "../components/Select";
import api from "../api";

export default function Credentials() {
  const [items, setItems] = useState([]);
  const [stats, setStats] = useState({ total: 0, normal: 0, disabled: 0 });
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [statusFilter, setStatusFilter] = useState("all");
  const [errorCodeFilter, setErrorCodeFilter] = useState("all");
  const [cooldownFilter, setCooldownFilter] = useState("all");
  const [tierFilter, setTierFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(new Set());
  const [message, setMessage] = useState(null);
  const [errorModal, setErrorModal] = useState(null);
  const [busyFiles, setBusyFiles] = useState(new Set());
  const [now, setNow] = useState(Date.now() / 1000);
  // 额度预览相关状态
  const [quotaModal, setQuotaModal] = useState(null); // { email } 额度弹窗
  const [quotaCache, setQuotaCache] = useState({}); // 缓存额度数据 { email: { claude, gemini } | { error } }

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);

  const fetchCreds = useCallback(async () => {
    setLoading(true);
    try {
      const offset = (page - 1) * pageSize;
      const res = await api.get("/creds/status", {
        params: {
          offset,
          limit: pageSize,
          status_filter: statusFilter,
          error_code_filter: errorCodeFilter,
          cooldown_filter: cooldownFilter,
          tier_filter: tierFilter,
        },
      });
      setItems(res.data.items);
      setTotal(res.data.total);
      setStats(res.data.stats || { total: 0, normal: 0, disabled: 0 });
      setSelected(new Set());
    } catch (err) {
      showMessage("error", err.response?.data?.detail || "加载凭证失败");
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, statusFilter, errorCodeFilter, cooldownFilter, tierFilter]);

  useEffect(() => {
    fetchCreds();
  }, [fetchCreds]);

  const showMessage = useCallback((type, text, title) => {
    setMessage({ type, text, title: title || (type === "success" ? "操作成功" : type === "error" ? "操作失败" : "提示") });
  }, []);

  const setBusy = (email, busy) => {
    setBusyFiles((prev) => {
      const next = new Set(prev);
      if (busy) next.add(email);
      else next.delete(email);
      return next;
    });
  };

  const doAction = async (email, action) => {
    setBusy(email, true);
    try {
      const res = await api.post("/creds/action", { email, action });
      showMessage("success", res.data.message);
      fetchCreds();
    } catch (err) {
      showMessage("error", err.response?.data?.detail || "操作失败");
    } finally {
      setBusy(email, false);
    }
  };

  const doBatchAction = async (action) => {
    if (selected.size === 0) return;
    setBusy("__batch__", true);
    try {
      const res = await api.post("/creds/batch-action", {
        action,
        emails: Array.from(selected),
      });
      showMessage(res.data.errors?.length ? "error" : "success", res.data.message);
      fetchCreds();
    } catch (err) {
      showMessage("error", err.response?.data?.detail || "批量操作失败");
    } finally {
      setBusy("__batch__", false);
    }
  };

  const verifyProject = async (email) => {
    setBusy(email, true);
    try {
      const res = await api.post(`/creds/verify-project/${encodeURIComponent(email)}`);
      if (res.data.success) {
        showMessage(
          "success",
          `Project=${res.data.project_id}, Tier=${res.data.subscription_tier}`,
          `检验成功: ${email}`
        );
      } else {
        showMessage("error", res.data.message || res.data.error || "检验失败", `检验失败: ${email}`);
      }
      fetchCreds();
    } catch (err) {
      showMessage(
        "error",
        err.response?.data?.detail || err.response?.data?.error || err.response?.data?.message || "检验失败",
        `检验失败: ${email}`
      );
    } finally {
      setBusy(email, false);
    }
  };

  const testCredential = async (email) => {
    setBusy(email, true);
    try {
      const res = await api.post(`/creds/test/${encodeURIComponent(email)}`);
      // 200 = 可用；429 = 限流但有效
      showMessage(
        "success",
        res.data.message || (res.status === 429 ? "凭证被限流（429）但有效" : "凭证测试通过"),
        `测试成功: ${email}`
      );
      fetchCreds();
    } catch (err) {
      const data = err.response?.data || {};
      const status = err.response?.status;
      // 透明透传上游错误：优先后端返回的 error 原文
      const upstreamError = data.error || data.detail || data.message || "测试失败";
      showMessage(
        status === 429 ? "info" : "error",
        `HTTP ${status}\n${upstreamError}`,
        `测试失败: ${email}`
      );
      fetchCreds();
    } finally {
      setBusy(email, false);
    }
  };

  // 额度弹窗：打开并强制刷新（不使用旧缓存）
  const openQuotaModal = async (email) => {
    setQuotaModal({ email });
    setBusy(email, true);
    try {
      const res = await api.get(`/creds/quota/${encodeURIComponent(email)}`);
      if (res.data.success) {
        const aggregated = aggregateQuota(res.data.quotaGroups || []);
        setQuotaCache((prev) => ({ ...prev, [email]: aggregated }));
      } else {
        setQuotaCache((prev) => ({ ...prev, [email]: { error: res.data.error || "获取额度失败" } }));
      }
    } catch (err) {
      setQuotaCache((prev) => ({
        ...prev,
        [email]: { error: err.response?.data?.detail || err.response?.data?.error || "获取额度失败" },
      }));
    } finally {
      setBusy(email, false);
    }
  };

  // 聚合额度分组 -> Claude / Gemini 各自的剩余百分比（多组取平均，组内取 weekly/5h 较小值）
  const aggregateQuota = (groups) => {
    const result = {
      claude: { remaining: 0, count: 0, resetTime: "" },
      gemini: { remaining: 0, count: 0, resetTime: "" },
    };

    groups.forEach((group) => {
      const name = (group.displayName || "").toLowerCase();
      const target = name.includes("claude") ? result.claude : result.gemini;
      target.count += 1;

      // 每组取 weekly / 5h 中较小的 remainingFraction（短板效应）
      let minPct = null;
      let minResetTime = "";
      (group.buckets || []).forEach((bucket) => {
        const pct = (bucket.remainingFraction || 0) * 100;
        if (minPct === null || pct < minPct) {
          minPct = pct;
          minResetTime = bucket.resetTime || "";
        }
      });

      if (minPct !== null) {
        target.remaining += Math.round(minPct * 10) / 10;
        if (!target.resetTime && minResetTime) {
          try {
            target.resetTime = new Date(minResetTime).toLocaleString("zh-CN");
          } catch {
            target.resetTime = minResetTime;
          }
        }
      }
    });

    // 多组时取平均值
    if (result.claude.count > 1)
      result.claude.remaining = Math.round(result.claude.remaining / result.claude.count);
    if (result.gemini.count > 1)
      result.gemini.remaining = Math.round(result.gemini.remaining / result.gemini.count);

    return result;
  };

  // 额度进度条颜色
  const getQuotaColor = (remaining) => {
    if (remaining >= 80) return { bar: "bg-green-500", text: "text-green-400" };
    if (remaining >= 40) return { bar: "bg-yellow-500", text: "text-yellow-400" };
    if (remaining >= 20) return { bar: "bg-orange-500", text: "text-orange-400" };
    return { bar: "bg-red-500", text: "text-red-400" };
  };

  const exportCredential = async (email) => {
    try {
      const res = await api.get(`/creds/export/${encodeURIComponent(email)}`, {
        responseType: "blob",
      });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${email}.json`;
      a.click();
      URL.revokeObjectURL(url);
      showMessage("success", `已导出 ${email}.json`);
    } catch (err) {
      showMessage("error", err.response?.data?.detail || "导出失败");
    }
  };

  const downloadAll = async () => {
    setBusy("__batch__", true);
    try {
      const res = await api.get("/creds/download-all", {
        responseType: "blob",
      });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = "antigravity_credentials.zip";
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      showMessage("error", err.response?.data?.detail || "下载失败");
    } finally {
      setBusy("__batch__", false);
    }
  };

  const openErrors = async (email) => {
    try {
      const res = await api.get(`/creds/errors/${encodeURIComponent(email)}`);
      setErrorModal(res.data);
    } catch (err) {
      showMessage("error", err.response?.data?.detail || "获取错误信息失败");
    }
  };

  const toggleSelect = (email) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(email)) next.delete(email);
      else next.add(email);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selected.size === items.length) setSelected(new Set());
    else setSelected(new Set(items.map((i) => i.email)));
  };

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const formatTime = (ts) => {
    if (!ts) return "-";
    return new Date(ts * 1000).toLocaleString();
  };

  const getCooldownText = (cooldowns) => {
    const active = Object.entries(cooldowns || {}).filter(
      ([, until]) => until > now
    );
    if (!active.length) return null;
    const longest = Math.max(...active.map(([, until]) => until));
    const remain = longest - now;
    const h = Math.floor(remain / 3600);
    const m = Math.floor((remain % 3600) / 60);
    const s = Math.floor(remain % 60);
    return h > 0 ? `${h}h${m}m` : m > 0 ? `${m}m${s}s` : `${s}s`;
  };

  const tierStyle = (tier) =>
    tier === "ultra"
      ? "bg-red-500/10 text-red-400 border-red-500/30"
      : tier === "pro"
      ? "bg-amber-500/10 text-amber-400 border-amber-500/30"
      : "bg-blue-500/10 text-blue-400 border-blue-500/30";

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* 消息弹窗 */}
      {message && (
        <Modal
          type={message.type}
          title={message.title}
          onClose={() => setMessage(null)}
        >
          <span className="whitespace-pre-wrap">{message.text}</span>
        </Modal>
      )}

      {/* 状态统计 */}
      <div className="grid grid-cols-3 gap-2 sm:gap-4">
        <div className="bg-dark-800 border border-dark-600 rounded-xl p-3 sm:p-4 text-center">
          <div className="text-xl sm:text-2xl font-bold text-white">{stats.total}</div>
          <div className="text-xs text-gray-500 mt-0.5">总计</div>
        </div>
        <div className="bg-emerald-900/20 border border-emerald-700/30 rounded-xl p-3 sm:p-4 text-center">
          <div className="text-xl sm:text-2xl font-bold text-emerald-400">{stats.normal}</div>
          <div className="text-xs text-gray-500 mt-0.5">正常</div>
        </div>
        <div className="bg-red-900/20 border border-red-700/30 rounded-xl p-3 sm:p-4 text-center">
          <div className="text-xl sm:text-2xl font-bold text-red-400">{stats.disabled}</div>
          <div className="text-xs text-gray-500 mt-0.5">禁用</div>
        </div>
      </div>

      {/* 操作按钮 */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={fetchCreds}
          disabled={loading}
          className="btn btn-secondary text-xs sm:text-sm px-3 py-1.5 sm:px-4 sm:py-2 flex items-center gap-1.5"
        >
          <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
          刷新状态
        </button>
        <button
          onClick={downloadAll}
          disabled={busyFiles.has("__batch__")}
          className="btn btn-secondary text-xs sm:text-sm px-3 py-1.5 sm:px-4 sm:py-2 flex items-center gap-1.5"
        >
          <Download size={15} />
          打包导出
        </button>
      </div>

      {/* 筛选器 */}
      <div className="grid grid-cols-2 sm:flex sm:flex-wrap gap-2 sm:gap-3 text-sm">
        <Select
          value={statusFilter}
          onChange={(v) => {
            setStatusFilter(v);
            setPage(1);
          }}
          className="w-full sm:w-32"
          options={[
            { value: "all", label: "全部凭证" },
            { value: "enabled", label: "仅启用" },
            { value: "disabled", label: "仅禁用" },
          ]}
        />
        <Select
          value={errorCodeFilter}
          onChange={(v) => {
            setErrorCodeFilter(v);
            setPage(1);
          }}
          className="w-full sm:w-32"
          options={[
            { value: "all", label: "错误码: 全部" },
            { value: "none", label: "无错误" },
            { value: "400", label: "400" },
            { value: "403", label: "403" },
            { value: "429", label: "429" },
            { value: "500", label: "500" },
          ]}
        />
        <Select
          value={cooldownFilter}
          onChange={(v) => {
            setCooldownFilter(v);
            setPage(1);
          }}
          className="w-full sm:w-32"
          options={[
            { value: "all", label: "冷却: 全部" },
            { value: "in_cooldown", label: "CD中" },
            { value: "no_cooldown", label: "未CD" },
          ]}
        />
        <Select
          value={tierFilter}
          onChange={(v) => {
            setTierFilter(v);
            setPage(1);
          }}
          className="w-full sm:w-32"
          options={[
            { value: "all", label: "Tier: 全部" },
            { value: "free", label: "Free" },
            { value: "pro", label: "Pro" },
            { value: "ultra", label: "Ultra" },
          ]}
        />
        <Select
          value={pageSize}
          onChange={(v) => {
            setPageSize(Number(v));
            setPage(1);
          }}
          className="w-full sm:w-32"
          options={[20, 50, 100, 200, 500, 1000].map((n) => ({
            value: n,
            label: `每页 ${n}`,
          }))}
        />
      </div>

      {/* 批量操作 */}
      <div className="card p-3 sm:p-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
          <label className="flex items-center gap-2 text-gray-400 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={selected.size === items.length && items.length > 0}
              onChange={toggleSelectAll}
              className="accent-purple-600 w-4 h-4"
            />
            全选
          </label>
          <span className="text-gray-500 text-xs">已选 {selected.size} 项</span>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => doBatchAction("enable")}
              disabled={selected.size === 0 || busyFiles.has("__batch__")}
              className="btn btn-secondary text-xs px-3 py-1.5 disabled:opacity-40"
            >
              批量启用
            </button>
            <button
              onClick={() => doBatchAction("disable")}
              disabled={selected.size === 0 || busyFiles.has("__batch__")}
              className="btn btn-secondary text-xs px-3 py-1.5 disabled:opacity-40"
            >
              批量禁用
            </button>
            <button
              onClick={() => doBatchAction("delete")}
              disabled={selected.size === 0 || busyFiles.has("__batch__")}
              className="btn btn-danger text-xs px-3 py-1.5 disabled:opacity-40"
            >
              批量删除
            </button>
          </div>
        </div>
      </div>

      {/* 凭证卡片列表 */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="animate-spin text-purple-500" size={28} />
        </div>
      ) : items.length === 0 ? (
        <div className="card text-center py-12 text-gray-500 text-sm">
          没有凭证。请先通过 OAuth 认证或批量导入添加凭证。
        </div>
      ) : (
        <div className="grid gap-3 sm:gap-4 grid-cols-1 md:grid-cols-2 xl:grid-cols-3">
          {items.map((cred) => {
            const cooldownText = getCooldownText(cred.model_cooldowns);
            const busy = busyFiles.has(cred.email);
            return (
              <div
                key={cred.email}
                className={`card p-3 sm:p-4 space-y-3 transition-colors ${
                  cred.disabled ? "border-red-500/30" : ""
                }`}
              >
                {/* 头部：选择框 + 邮箱 + 状态标签 */}
                <div className="flex items-start gap-2.5">
                  <input
                    type="checkbox"
                    checked={selected.has(cred.email)}
                    onChange={() => toggleSelect(cred.email)}
                    className="accent-purple-600 w-4 h-4 flex-shrink-0 mt-0.5"
                  />
                  <div className="flex-1 min-w-0 space-y-1.5">
                    <div className="text-sm font-medium text-white break-all leading-snug">
                      {cred.email}
                    </div>
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span
                        className={`text-xs px-2 py-0.5 rounded-full border ${tierStyle(cred.tier)}`}
                      >
                        {cred.tier || "pro"}
                      </span>
                      {cred.disabled ? (
                        <span className="text-red-400 text-xs flex items-center gap-1">
                          <Ban size={12} /> 禁用
                        </span>
                      ) : (
                        <span className="text-emerald-400 text-xs flex items-center gap-1">
                          <CheckCircle size={12} /> 启用
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                {/* 信息区 */}
                <div className="space-y-1 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    {cooldownText ? (
                      <span className="text-cyan-400 flex items-center gap-1">
                        <Snowflake size={12} /> CD {cooldownText}
                      </span>
                    ) : (
                      <span className="text-gray-600 flex items-center gap-1">
                        <Clock size={12} /> 无CD
                      </span>
                    )}
                    {cred.error_codes?.length ? (
                      <button
                        onClick={() => openErrors(cred.email)}
                        className="text-red-400 hover:text-red-300 flex items-center gap-1"
                      >
                        <AlertCircle size={12} />
                        {cred.error_codes.join(",")}
                      </button>
                    ) : (
                      <span className="text-gray-600 flex items-center gap-1">
                        <CheckCircle size={12} /> 无错误
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1 text-gray-500">
                    <Clock size={12} className="flex-shrink-0" />
                    <span className="truncate" title={formatTime(cred.last_success)}>
                      {formatTime(cred.last_success)}
                    </span>
                  </div>
                </div>

                {/* 操作按钮（2×3 网格铺满卡片） */}
                <div className="grid grid-cols-3 gap-1.5 pt-1 border-t border-dark-700">
                  {busy ? (
                    <div className="col-span-3 flex items-center justify-center py-1.5">
                      <Loader2 size={16} className="animate-spin text-purple-400" />
                    </div>
                  ) : (
                    <>
                      <button
                        onClick={() => verifyProject(cred.email)}
                        className="text-xs px-2 py-1.5 rounded-lg bg-dark-700 hover:bg-dark-600 text-gray-300 flex items-center justify-center gap-1"
                        title="检验 Project ID / Tier（可修复 403）"
                      >
                        <Shield size={13} /> 检验
                      </button>
                      <button
                        onClick={() => testCredential(cred.email)}
                        className="text-xs px-2 py-1.5 rounded-lg bg-dark-700 hover:bg-dark-600 text-gray-300 flex items-center justify-center gap-1"
                        title="测试凭证"
                      >
                        <Zap size={13} /> 测试
                      </button>
                      <button
                        onClick={() => openQuotaModal(cred.email)}
                        disabled={cred.disabled}
                        className="text-xs px-2 py-1.5 rounded-lg bg-dark-700 hover:bg-dark-600 text-gray-300 flex items-center justify-center gap-1 disabled:opacity-40"
                        title="查看额度"
                      >
                        <BarChart3 size={13} /> 额度
                      </button>
                      <button
                        onClick={() => exportCredential(cred.email)}
                        className="text-xs px-2 py-1.5 rounded-lg bg-dark-700 hover:bg-dark-600 text-gray-300 flex items-center justify-center gap-1"
                        title="导出凭证（邮箱.json）"
                      >
                        <Download size={13} /> 导出
                      </button>
                      <button
                        onClick={() =>
                          doAction(cred.email, cred.disabled ? "enable" : "disable")
                        }
                        className={`text-xs px-2 py-1.5 rounded-lg flex items-center justify-center gap-1 ${
                          cred.disabled
                            ? "bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-400"
                            : "bg-amber-600/20 hover:bg-amber-600/30 text-amber-400"
                        }`}
                      >
                        {cred.disabled ? <CheckCircle size={13} /> : <Ban size={13} />}
                        {cred.disabled ? "启用" : "禁用"}
                      </button>
                      <button
                        onClick={() => doAction(cred.email, "delete")}
                        className="text-xs px-2 py-1.5 rounded-lg bg-red-600/10 hover:bg-red-600/20 text-red-400 flex items-center justify-center gap-1"
                        title="删除"
                      >
                        <Trash2 size={13} /> 删除
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* 分页 */}
      {total > pageSize && (
        <div className="flex items-center justify-between text-sm">
          <span className="text-gray-500 text-xs sm:text-sm">
            共 {total} 个凭证，第 {page}/{totalPages} 页
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="btn btn-secondary text-xs px-3 py-1.5 disabled:opacity-40"
            >
              上一页
            </button>
            <span className="text-gray-400 text-xs sm:text-sm">
              {page} / {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="btn btn-secondary text-xs px-3 py-1.5 disabled:opacity-40"
            >
              下一页
            </button>
          </div>
        </div>
      )}


      {/* 额度弹窗 */}
      {quotaModal && (
        <Modal
          type="info"
          title={`额度信息 - ${quotaModal.email}`}
          onClose={() => setQuotaModal(null)}
        >
          {busyFiles.has(quotaModal.email) ? (
            <div className="flex items-center justify-center py-6 text-gray-500">
              <RefreshCw size={16} className="animate-spin mr-2" />
              加载额度中...
            </div>
          ) : quotaCache[quotaModal.email]?.error ? (
            <div className="text-center py-4 text-red-400">{quotaCache[quotaModal.email].error}</div>
          ) : quotaCache[quotaModal.email] ? (
            <div className="space-y-3">
              {quotaCache[quotaModal.email].claude?.count > 0 && (
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-purple-400 text-sm">Claude</span>
                    <span className={`text-sm font-medium ${getQuotaColor(quotaCache[quotaModal.email].claude.remaining).text}`}>
                      {quotaCache[quotaModal.email].claude.remaining.toFixed(1)}%
                    </span>
                  </div>
                  <div className="bg-dark-600 rounded-full h-2">
                    <div
                      className={`h-2 rounded-full ${getQuotaColor(quotaCache[quotaModal.email].claude.remaining).bar}`}
                      style={{ width: `${Math.min(quotaCache[quotaModal.email].claude.remaining, 100)}%` }}
                    />
                  </div>
                </div>
              )}
              {quotaCache[quotaModal.email].gemini?.count > 0 && (
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-cyan-400 text-sm">Gemini</span>
                    <span className={`text-sm font-medium ${getQuotaColor(quotaCache[quotaModal.email].gemini.remaining).text}`}>
                      {quotaCache[quotaModal.email].gemini.remaining.toFixed(1)}%
                    </span>
                  </div>
                  <div className="bg-dark-600 rounded-full h-2">
                    <div
                      className={`h-2 rounded-full ${getQuotaColor(quotaCache[quotaModal.email].gemini.remaining).bar}`}
                      style={{ width: `${Math.min(quotaCache[quotaModal.email].gemini.remaining, 100)}%` }}
                    />
                  </div>
                </div>
              )}
              {!quotaCache[quotaModal.email].claude?.count &&
                !quotaCache[quotaModal.email].gemini?.count && (
                  <div className="text-center py-3 text-gray-500">暂无额度数据</div>
                )}
              {(quotaCache[quotaModal.email].claude?.resetTime ||
                quotaCache[quotaModal.email].gemini?.resetTime) && (
                <div className="text-xs text-gray-500 text-right pt-1">
                  重置:{" "}
                  {quotaCache[quotaModal.email].claude?.resetTime ||
                    quotaCache[quotaModal.email].gemini?.resetTime}
                </div>
              )}
              <button
                onClick={() => openQuotaModal(quotaModal.email)}
                disabled={busyFiles.has(quotaModal.email)}
                className="w-full text-xs text-gray-400 hover:text-white py-2 rounded-lg bg-dark-700 hover:bg-dark-600 flex items-center justify-center gap-1.5 disabled:opacity-50"
              >
                <RefreshCw size={12} className={busyFiles.has(quotaModal.email) ? "animate-spin" : ""} />
                刷新额度
              </button>
            </div>
          ) : (
            <div className="text-center py-4 text-gray-500">加载中...</div>
          )}
        </Modal>
      )}

      {/* 错误详情弹窗 */}
      {errorModal && (
        <div
          className="fixed inset-0 bg-black/60 flex items-center justify-center p-4 z-50"
          onClick={() => setErrorModal(null)}
        >
          <div
            className="bg-dark-900 border border-dark-600 rounded-xl max-w-2xl w-full max-h-[80vh] overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 sm:px-5 py-4 border-b border-dark-700">
              <h3 className="font-semibold flex items-center gap-2 text-sm sm:text-base break-all">
                <AlertCircle size={16} className="text-red-400 flex-shrink-0" />
                错误详情 - {errorModal.email}
              </h3>
              <button
                onClick={() => setErrorModal(null)}
                className="text-gray-400 hover:text-white flex-shrink-0"
              >
                ✕
              </button>
            </div>
            <div className="p-4 sm:p-5 overflow-y-auto space-y-4">
              <div>
                <div className="text-sm text-gray-400 mb-1">错误码</div>
                <div className="text-red-400 text-sm">
                  {(errorModal.error_codes || []).join(", ") || "无"}
                </div>
              </div>
              <div>
                <div className="text-sm text-gray-400 mb-1">错误信息</div>
                <pre className="bg-dark-800 border border-dark-600 rounded-lg p-3 sm:p-4 text-xs text-gray-300 overflow-auto max-h-56 whitespace-pre-wrap">
                  {JSON.stringify(errorModal.error_messages, null, 2)}
                </pre>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={() => {
                    verifyProject(errorModal.email);
                    setErrorModal(null);
                  }}
                  className="btn btn-primary text-sm"
                >
                  检验修复（重新获取Project ID）
                </button>
                <button
                  onClick={() => {
                    testCredential(errorModal.email);
                    setErrorModal(null);
                  }}
                  className="btn btn-secondary text-sm"
                >
                  测试凭证
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
