import {
    LogOut,
    Rocket,
    Settings,
    KeyRound,
    Upload,
    Info,
    BarChart3,
} from "lucide-react";
import { useEffect, useState } from "react";
import api from "./api";
import Login from "./pages/Login";
import OAuth from "./pages/OAuth";
import Credentials from "./pages/Credentials";
import UploadPage from "./pages/Upload";
import Config from "./pages/Config";
import About from "./pages/About";
import UsageLogs from "./pages/UsageLogs";

export default function App() {
  const [loggedIn, setLoggedIn] = useState(() => !!localStorage.getItem("panelToken"));
  const [activeTab, setActiveTab] = useState("oauth");
  const [version, setVersion] = useState(null);
  const [checkingUpdate, setCheckingUpdate] = useState(false);

  const checkUpdate = () => {
    setCheckingUpdate(true);
    api
      .get("/version/info", { params: { check_update: "true" } })
      .then((res) => setVersion(res.data))
      .catch(() => {})
      .finally(() => setCheckingUpdate(false));
  };

  useEffect(() => {
    api
      .get("/version/info")
      .then((res) => setVersion(res.data))
      .catch(() => {});
  }, []);

  const logout = () => {
    localStorage.removeItem("panelToken");
    setLoggedIn(false);
  };

  if (!loggedIn) {
    return <Login onSuccess={() => setLoggedIn(true)} />;
  }

  const tabs = [
    { id: "oauth", label: "OAuth 认证", icon: Rocket },
    { id: "credentials", label: "凭证管理", icon: KeyRound },
    { id: "upload", label: "批量导入", icon: Upload },
    { id: "usage-logs", label: "请求日志", icon: BarChart3 },
    { id: "config", label: "配置管理", icon: Settings },
    { id: "about", label: "项目信息", icon: Info },
  ];

  return (
    <div className="min-h-screen">
      {/* 导航栏 */}
      <nav className="bg-dark-900 border-b border-dark-700 sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div>
                <span className="text-xl font-bold">AGY2API</span>
                {version?.version && (
                  <span className="ml-2 text-xs text-gray-500">v{version.version}</span>
                )}
              </div>
            </div>
            <button
              onClick={logout}
              className="px-3 py-1.5 sm:px-4 sm:py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg flex items-center gap-1 sm:gap-2 text-sm"
            >
              <LogOut size={16} />
              <span className="hidden sm:inline">退出登录</span>
            </button>
          </div>

          {/* Tab 导航 */}
          <div className="flex items-center gap-1 sm:gap-2 mt-4 overflow-x-auto pb-1">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`px-3 sm:px-4 py-2 text-xs sm:text-sm font-medium rounded-lg whitespace-nowrap flex items-center gap-1.5 transition-colors ${
                    activeTab === tab.id
                      ? "bg-purple-600 text-white"
                      : "text-gray-400 hover:text-white hover:bg-dark-700"
                  }`}
                >
                  <Icon size={15} />
                  {tab.label}
                </button>
              );
            })}
          </div>
        </div>
      </nav>

      <div className="max-w-6xl mx-auto px-4 py-6">
        {activeTab === "oauth" && <OAuth />}
        {activeTab === "credentials" && <Credentials />}
        {activeTab === "upload" && <UploadPage />}
        {activeTab === "usage-logs" && <UsageLogs />}
        {activeTab === "config" && <Config />}
        {activeTab === "about" && <About version={version} onCheckUpdate={checkUpdate} checkingUpdate={checkingUpdate} />}
      </div>
    </div>
  );
}
