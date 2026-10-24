import {
  AlertTriangle,
  BarChart3,
  CheckCircle,
  Clock,
  Cpu,
  Database,
  FileText,
  Loader2,
  RefreshCw,
  Trash2,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import Modal from "../components/Modal";
import Select from "../components/Select";
import api from "../api";

const PAGE_SIZES = [20, 50, 100, 200].map((n) => ({ value: n, label: `每页 ${n}` }));

function formatTime(ts) {
  const d = new Date(ts * 1000);
  const pad = (x) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function formatTokens(n) {
  if (!n) return "0";
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(n);
}

function formatMaybeJson(text) {
  if (typeof text !== "string") return JSON.stringify(text ?? null, null, 2);
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

export default function UsageLogs() {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [statusFilter, setStatusFilter] = useState("all");
  const [formatFilter, setFormatFilter] = useState("");
  const [modelFilter, setModelFilter] = useState("");
  const [statusCodeFilter, setStatusCodeFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [clearing, setClearing] = useState(false);
  const [message, setMessage] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailBody, setDetailBody] = useState(null);
  const [detailBodyLoading, setDetailBodyLoading] = useState(false);

  // 时间范围（小时）
  const [rangeHours, setRangeHours] = useState(24);

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params = {
        offset: (page - 1) * pageSize,
        limit: pageSize,
        status: statusFilter,
      };
      if (formatFilter) params.api_format = formatFilter;
      if (modelFilter.trim()) params.model_name = modelFilter.trim();
      const code = parseInt(statusCodeFilter, 10);
      if (statusCodeFilter.trim() !== "" && Number.isInteger(code)) params.status_code = code;
      if (rangeHours > 0) params.start_timestamp = Math.floor(Date.now() / 1000) - rangeHours * 3600;

      const res = await api.get("/usage-logs/list", { params });
      setItems(res.data.items);
      setTotal(res.data.total);
    } catch (err) {
      setMessage({ type: "error", text: err.response?.data?.detail || "加载日志失败" });
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, statusFilter, formatFilter, modelFilter, statusCodeFilter, rangeHours]);

  const fetchStats = useCallback(async () => {
    try {
      const params = {};
      if (rangeHours > 0) params.start_timestamp = Math.floor(Date.now() / 1000) - rangeHours * 3600;
      if (formatFilter) params.api_format = formatFilter;
      if (modelFilter.trim()) params.model_name = modelFilter.trim();
      const res = await api.get("/usage-logs/stats", { params });
      setStats(res.data);
    } catch (err) {
      /* 统计失败不阻塞 */
    }
  }, [rangeHours, formatFilter, modelFilter]);

  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  useEffect(() => {
    fetchStats();
  }, [fetchStats]);

  const refresh = () => {
    fetchLogs();
    fetchStats();
  };

  const clearLogs = async () => {
    if (!window.confirm("确定要清理 3 天前的日志吗？此操作不可恢复。")) return;
    setClearing(true);
    try {
      const res = await api.post("/usage-logs/clear", null, { params: { days: 3 } });
      setMessage({ type: "success", text: res.data.message || "清理成功" });
      refresh();
    } catch (err) {
      setMessage({ type: "error", text: err.response?.data?.detail || "清理失败" });
    } finally {
      setClearing(false);
    }
  };

  const truncateLogs = async () => {
    if (!window.confirm("确定要清空全部请求日志吗？\n整张日志表将被删除重建，不留任何残留，此操作不可恢复。")) return;
    setClearing(true);
    try {
      const res = await api.post("/usage-logs/truncate");
      setMessage({ type: "success", text: res.data.message || "已清空全部日志" });
      refresh();
    } catch (err) {
      setMessage({ type: "error", text: err.response?.data?.detail || "清空失败" });
    } finally {
      setClearing(false);
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  // 请求体详细日志：按索引从磁盘读取
  const openRequestBody = async () => {
    if (!detail?.request_log_file) return;
    setDetailBodyLoading(true);
    try {
      const res = await api.get("/usage-logs/request-body", { params: { path: detail.request_log_file } });
      setDetailBody(res.data);
    } catch (err) {
      // 文件可能已被清理或手动删除，仅在详情弹窗内提示，不弹全局错误
      setDetailBody({ __missing: true, __message: err.response?.data?.detail || "详细日志文件不存在或已清理" });
    } finally {
      setDetailBodyLoading(false);
    }
  };

  const copyDetailBody = async () => {
    if (!detailBody || detailBody.__missing) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(detailBody, null, 2));
      setMessage({ type: "success", text: "已复制详细日志 JSON" });
    } catch {
      setMessage({ type: "error", text: "复制失败" });
    }
  };

  return (
    <div className="space-y-4">
      {/* 统计卡片 */}
      {stats && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <div className="card p-4 flex items-center gap-3">
            <div className="p-2.5 bg-purple-600/20 rounded-lg">
              <Database size={20} className="text-purple-400" />
            </div>
            <div className="min-w-0">
              <div className="text-xl font-bold">{stats.total_requests}</div>
              <div className="text-xs text-gray-500">总请求</div>
            </div>
          </div>
          <div className="card p-4 flex items-center gap-3">
            <div className="p-2.5 bg-green-600/20 rounded-lg">
              <CheckCircle size={20} className="text-green-400" />
            </div>
            <div className="min-w-0">
              <div className="text-xl font-bold text-green-400">{stats.success_count}</div>
              <div className="text-xs text-gray-500">成功</div>
            </div>
          </div>
          <div className="card p-4 flex items-center gap-3">
            <div className="p-2.5 bg-red-600/20 rounded-lg">
              <XCircle size={20} className="text-red-400" />
            </div>
            <div className="min-w-0">
              <div className="text-xl font-bold text-red-400">{stats.error_count}</div>
              <div className="text-xs text-gray-500">失败</div>
            </div>
          </div>
          <div className="card p-4 flex items-center gap-3">
            <div className="p-2.5 bg-blue-600/20 rounded-lg">
              <Cpu size={20} className="text-blue-400" />
            </div>
            <div className="min-w-0">
              <div className="text-xl font-bold">{formatTokens(stats.total_tokens)}</div>
              <div className="text-xs text-gray-500">总 Tokens</div>
            </div>
          </div>
        </div>
      )}

      {/* 操作栏 */}
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={refresh} disabled={loading} className="btn btn-secondary text-xs sm:text-sm px-3 py-1.5 sm:px-4 sm:py-2 flex items-center gap-1.5">
          <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
          刷新
        </button>
        <button onClick={clearLogs} disabled={clearing} className="btn btn-secondary text-xs sm:text-sm px-3 py-1.5 sm:px-4 sm:py-2 flex items-center gap-1.5">
          {clearing ? <Loader2 size={15} className="animate-spin" /> : <Trash2 size={15} />}
          清理3天前日志
        </button>
        <button onClick={truncateLogs} disabled={clearing} className="btn btn-danger text-xs sm:text-sm px-3 py-1.5 sm:px-4 sm:py-2 flex items-center gap-1.5">
          <Trash2 size={15} />
          清空全部日志
        </button>
      </div>

      {/* 消息弹窗 */}
      {message && (
        <Modal type={message.type} title={message.type === "success" ? "操作成功" : "操作失败"} onClose={() => setMessage(null)}>
          <span className="whitespace-pre-wrap break-all">{message.text}</span>
        </Modal>
      )}

      {/* 筛选器 */}
      <div className="grid grid-cols-2 sm:flex sm:flex-wrap gap-2 sm:gap-3 text-sm">
        <Select
          value={rangeHours}
          onChange={(v) => { setRangeHours(Number(v)); setPage(1); }}
          className="w-full sm:w-32"
          options={[
            { value: 1, label: "最近 1 小时" },
            { value: 6, label: "最近 6 小时" },
            { value: 24, label: "最近 24 小时" },
            { value: 24 * 7, label: "最近 7 天" },
            { value: 24 * 30, label: "最近 30 天" },
            { value: 0, label: "全部时间" },
          ]}
        />
        <Select
          value={statusFilter}
          onChange={(v) => { setStatusFilter(v); setPage(1); }}
          className="w-full sm:w-32"
          options={[
            { value: "all", label: "状态: 全部" },
            { value: "success", label: "仅成功" },
            { value: "error", label: "仅失败" },
          ]}
        />
        <Select
          value={formatFilter}
          onChange={(v) => { setFormatFilter(v); setPage(1); }}
          className="w-full sm:w-32"
          options={[
            { value: "", label: "格式: 全部" },
            { value: "openai", label: "OpenAI" },
            { value: "gemini", label: "Gemini" },
            { value: "anthropic", label: "Claude" },
          ]}
        />
        <input
          value={modelFilter}
          onChange={(e) => { setModelFilter(e.target.value); setPage(1); }}
          placeholder="模型名筛选"
          className="w-full sm:w-40 bg-dark-800 border border-dark-600 rounded-lg px-3 py-2 text-gray-200 text-xs sm:text-sm placeholder-gray-500"
        />
        <input
          value={statusCodeFilter}
          onChange={(e) => { setStatusCodeFilter(e.target.value); setPage(1); }}
          placeholder="错误码筛选"
          inputMode="numeric"
          className="w-full sm:w-28 bg-dark-800 border border-dark-600 rounded-lg px-3 py-2 text-gray-200 text-xs sm:text-sm placeholder-gray-500"
        />
        <Select
          value={pageSize}
          onChange={(v) => { setPageSize(Number(v)); setPage(1); }}
          className="w-full sm:w-32"
          options={PAGE_SIZES}
        />
      </div>

      {/* 列表 */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="animate-spin text-purple-500" size={28} />
        </div>
      ) : items.length === 0 ? (
        <div className="card p-8 text-center text-gray-500">暂无请求日志</div>
      ) : (
        <div className="space-y-2">
          {items.map((item) => (
            <button
              key={item.id}
              onClick={() => { setDetail(item); setDetailBody(null); }}
              className="card p-3 sm:p-4 w-full text-left hover:border-purple-500/40 transition-colors cursor-pointer"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`text-xs px-1.5 py-0.5 rounded ${item.success ? "bg-green-500/15 text-green-400" : "bg-red-500/15 text-red-400"}`}>
                      {item.success ? "✓ 成功" : `✗ ${item.status_code || "失败"}`}
                    </span>
                    <span className="text-sm font-medium text-gray-200 truncate">{item.model_name || "(unknown)"}</span>
                      {item.upstream_model_name && item.upstream_model_name !== item.model_name && (
                        <span className="text-xs text-gray-500 truncate" title="后端真实模型名">→ {item.upstream_model_name}</span>
                      )}
                      <span className="text-xs px-1.5 py-0.5 rounded bg-dark-700 text-gray-400">
                        {item.api_format === "openai" ? "OpenAI" : item.api_format === "gemini" ? "Gemini" : item.api_format === "anthropic" ? "Claude" : item.api_format || "?"}
                      </span>
                      {item.is_stream && <span className="text-xs px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-400">流式</span>}
                      {item.request_log_file && <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400">详细</span>}
                    </div>
                  <div className="text-xs text-gray-500 mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                    <span className="flex items-center gap-1"><Clock size={11} />{formatTime(item.created_at)}</span>
                    <span>{(item.use_time || 0).toFixed(1)}s</span>
                    <span>{item.credential_email || "-"}</span>
                  </div>
                </div>
                <div className="text-right shrink-0 text-xs text-gray-400">
                  <div>{formatTokens(item.total_tokens)} tokens</div>
                  <div className="text-gray-600">
                    ↑{formatTokens(item.prompt_tokens)} ↓{formatTokens(item.completion_tokens)}
                  </div>
                </div>
              </div>
            </button>
          ))}
        </div>
      )}

      {/* 分页 */}
      {total > 0 && (
        <div className="flex items-center justify-between text-sm text-gray-400">
          <span>共 {total} 条</span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="btn btn-secondary text-xs px-3 py-1.5 disabled:opacity-40"
            >
              上一页
            </button>
            <span>{page} / {totalPages}</span>
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

      {/* 详情弹窗 */}
      {detail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60" onClick={() => setDetail(null)}>
          <div className="card max-w-lg w-full max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-base font-semibold text-purple-400">请求详情</h3>
              <button onClick={() => setDetail(null)} className="text-gray-400 hover:text-white">✕</button>
            </div>
            <div className="space-y-2 text-sm">
              {[
                ["时间", formatTime(detail.created_at)],
                ["请求 ID", detail.request_id || "-"],
                ["状态", detail.success ? "成功" : `失败 (${detail.status_code})`],
                ["API 格式", detail.api_format || "-"],
                ["客户端 IP", detail.client_ip || "-"],
                ["请求模型", detail.model_name || "-"],
                ["真实模型", detail.upstream_model_name || detail.model_name || "-"],
                ["凭证", detail.credential_email || "-"],
                ["流式", detail.is_stream ? "是" : "否"],
                ["耗时", `${(detail.use_time || 0).toFixed(2)}s`],
                ["Prompt Tokens", String(detail.prompt_tokens)],
                ["Completion Tokens", String(detail.completion_tokens)],
                ["Total Tokens", String(detail.total_tokens)],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between gap-4 border-b border-dark-700 pb-1.5">
                  <span className="text-gray-500 shrink-0">{k}</span>
                  <span className="text-gray-200 text-right break-all">{v}</span>
                </div>
              ))}
              {detail.error_message && (
                <div>
                  <div className="text-gray-500 mb-1">错误信息</div>
                  <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-2.5 text-xs text-red-300 break-all">
                    {detail.error_message}
                  </div>
                </div>
              )}
              {detail.request_log_file && (
                <div className="pt-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-gray-500">请求体详细日志</div>
                    <button
                      onClick={openRequestBody}
                      disabled={detailBodyLoading}
                      className="btn btn-secondary text-xs px-3 py-1.5 flex items-center gap-1.5"
                    >
                      {detailBodyLoading ? <Loader2 size={13} className="animate-spin" /> : <FileText size={13} />}
                      查看详细内容
                    </button>
                  </div>
                  <p className="text-xs text-gray-500 mt-1.5">
                    命中指定错误码时保存了发往上游的完整请求头与请求体，用于排查请求体错误（如 400）
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 请求体详细日志弹窗 */}
      {detailBody && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/70" onClick={() => setDetailBody(null)}>
          <div className="card max-w-4xl w-full max-h-[85vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            {detailBody.__missing ? (
              <div className="py-8 text-center text-sm text-gray-400">
                <AlertTriangle size={28} className="mx-auto mb-3 text-amber-400" />
                <div>{detailBody.__message}</div>
                <div className="text-xs text-gray-500 mt-2">磁盘上的详细日志文件可能已被清理或手动删除</div>
              </div>
            ) : (
              <>
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-base font-semibold text-purple-400">请求体详细日志</h3>
              <div className="flex items-center gap-2">
                <button onClick={copyDetailBody} className="btn btn-secondary text-xs px-3 py-1.5">
                  复制 JSON
                </button>
                <button onClick={() => setDetailBody(null)} className="text-gray-400 hover:text-white">✕</button>
              </div>
            </div>
            <p className="text-xs text-gray-500 mb-3">
              命中指定错误码时保存，用于排查请求体错误：完整记录发往上游的请求头（已脱敏）与请求体。响应体为上游返回的原始错误。
            </p>
            <div className="space-y-3 text-xs">
              <div>
                <div className="text-gray-400 mb-1">请求 URL</div>
                <pre className="bg-dark-800 border border-dark-600 rounded-lg p-2.5 overflow-x-auto text-gray-200 whitespace-pre-wrap break-all">{detailBody.url || "-"}</pre>
              </div>
              <div>
                <div className="text-gray-400 mb-1">请求头（已脱敏）</div>
                <pre className="bg-dark-800 border border-dark-600 rounded-lg p-2.5 overflow-x-auto text-gray-200">{JSON.stringify(detailBody.request_headers || {}, null, 2)}</pre>
              </div>
              <div>
                <div className="text-gray-400 mb-1">请求体</div>
                <pre className="bg-dark-800 border border-dark-600 rounded-lg p-2.5 overflow-x-auto text-gray-200">{JSON.stringify(detailBody.request_body ?? {}, null, 2)}</pre>
              </div>
              {detailBody.response_body && (
                <div>
                  <div className="text-gray-400 mb-1">上游响应</div>
                  <pre className="bg-dark-800 border border-dark-600 rounded-lg p-2.5 overflow-x-auto text-gray-200 whitespace-pre-wrap break-all">{formatMaybeJson(detailBody.response_body)}</pre>
                </div>
              )}
              {detailBody.error_message && (
                <div>
                  <div className="text-gray-400 mb-1">错误摘要</div>
                  <pre className="bg-red-500/10 border border-red-500/30 rounded-lg p-2.5 overflow-x-auto text-red-300 whitespace-pre-wrap break-all">{detailBody.error_message}</pre>
                </div>
              )}
            </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
