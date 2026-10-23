import axios from "axios";

const api = axios.create({
  baseURL: "",
  timeout: 120000,
});

// 请求拦截器 - 添加token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("panelToken");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 响应拦截器 - 处理错误
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem("panelToken");
      window.location.href = "/";
    }
    return Promise.reject(error);
  }
);

export default api;
