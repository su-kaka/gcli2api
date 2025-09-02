"""
Memory Manager - 内存监控和控制系统
"""
import gc
import threading
import time
import weakref
from dataclasses import dataclass
from threading import Lock
from typing import Dict, Any, Optional, Callable

import psutil

from config import get_config_value
from log import log

# 检查是否启用内存监控
def _is_memory_monitoring_enabled() -> bool:
    """检查是否启用内存监控"""
    try:
        return get_config_value('auto_start_memory_monitor', 'false', 'AUTO_START_MEMORY_MONITOR').lower() == 'true'
    except Exception:
        return False

# 全局开关
MEMORY_MONITORING_ENABLED = _is_memory_monitoring_enabled()

@dataclass
class MemoryConfig:
    """内存配置"""
    max_memory_mb: int = 100  # 最大内存限制(MB)
    warning_threshold: float = 0.90  # 警告阈值(90%)
    critical_threshold: float = 0.95  # 紧急阈值(95%)
    check_interval: int = 30  # 检查间隔(秒)
    gc_interval: int = 60  # GC间隔(秒)

class DisabledMemoryManager:
    """禁用版本的内存管理器 - 所有操作都是空操作"""
    
    def __init__(self):
        self.is_monitoring = False
        self.cleanup_count = 0
        self.peak_memory_mb = 0
    
    def get_memory_usage(self) -> Dict[str, float]:
        """返回空的内存使用情况"""
        return {'rss_mb': 0, 'usage_percent': 0, 'limit_mb': 100, 'available_mb': 100, 'peak_mb': 0}
    
    def is_memory_pressure(self) -> bool:
        """始终返回False"""
        return False
    
    def is_memory_emergency(self) -> bool:
        """始终返回False"""
        return False
    
    def register_cleanup_handler(self, name: str, handler: Callable):
        """空操作"""
        pass
    
    def register_cache_manager(self, name: str, manager: Any):
        """空操作"""
        pass
    
    def force_cleanup(self) -> Dict[str, Any]:
        """返回空的清理结果"""
        return {
            'freed_mb': 0,
            'before_mb': 0,
            'after_mb': 0,
            'cleaned_items': {},
            'cleanup_count': 0
        }
    
    def start_monitoring(self):
        """空操作"""
        pass
    
    def stop_monitoring(self):
        """空操作"""
        pass

class MemoryManager:
    """内存管理器 - 单例模式"""
    
    _instance = None
    _lock = Lock()
    
    def __new__(cls):
        # 如果内存监控未启用，返回禁用版本
        if not MEMORY_MONITORING_ENABLED:
            return DisabledMemoryManager()
            
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        
        if not MEMORY_MONITORING_ENABLED:
            log.debug("内存监控未启用，跳过内存管理器初始化")
            return
            
        self._initialized = True
        self.config = MemoryConfig()
        self.process = psutil.Process()
        
        # 监控状态
        self.is_monitoring = False
        self.monitor_thread: Optional[threading.Thread] = None
        
        # 注册的清理函数
        self.cleanup_handlers: Dict[str, Callable] = {}
        self.cache_managers: Dict[str, Any] = {}
        
        # 统计信息
        self.last_check_time = 0
        self.last_gc_time = 0
        self.cleanup_count = 0
        self.peak_memory_mb = 0
        
        # 从配置加载设置
        self._load_config()
        
        log.info(f"内存管理器初始化完成，最大内存限制: {self.config.max_memory_mb}MB")
    
    def _load_config(self):
        """从环境变量加载内存配置"""
        try:
            max_memory = int(get_config_value('max_memory_mb', '100', 'MAX_MEMORY_MB'))
            self.config.max_memory_mb = max_memory
            
            warning_threshold = float(get_config_value('memory_warning_threshold', '0.9', 'MEMORY_WARNING_THRESHOLD'))
            self.config.warning_threshold = warning_threshold

            critical_threshold = float(get_config_value('memory_critical_threshold', '0.95', 'MEMORY_CRITICAL_THRESHOLD'))
            self.config.critical_threshold = critical_threshold

            check_interval = int(get_config_value('memory_check_interval', '10', 'MEMORY_CHECK_INTERVAL'))
            self.config.check_interval = check_interval
            
        except (ValueError, TypeError) as e:
            log.warning(f"加载内存配置失败，使用默认值: {e}")
    
    def get_memory_usage(self) -> Dict[str, float]:
        """获取当前物理内存使用情况"""
        try:
            memory_info = self.process.memory_info()
            rss_mb = memory_info.rss / 1024 / 1024  # 物理内存(MB)
            
            # 更新峰值内存
            if rss_mb > self.peak_memory_mb:
                self.peak_memory_mb = rss_mb
            
            return {
                'rss_mb': rss_mb,
                'usage_percent': rss_mb / self.config.max_memory_mb,
                'limit_mb': self.config.max_memory_mb,
                'available_mb': self.config.max_memory_mb - rss_mb,
                'peak_mb': self.peak_memory_mb
            }
        except Exception as e:
            log.error(f"获取内存使用情况失败: {e}")
            return {'rss_mb': 0, 'usage_percent': 0, 'limit_mb': self.config.max_memory_mb, 'available_mb': self.config.max_memory_mb, 'peak_mb': 0}
    
    def is_memory_pressure(self) -> bool:
        """检查是否存在内存压力"""
        usage = self.get_memory_usage()
        return usage['usage_percent'] > self.config.warning_threshold
    
    def is_memory_emergency(self) -> bool:
        """检查内存是否达到紧急状态"""
        usage = self.get_memory_usage()
        return usage['usage_percent'] > self.config.critical_threshold

    def register_cleanup_handler(self, name: str, handler: Callable):
        """注册清理函数"""
        self.cleanup_handlers[name] = handler
        log.debug(f"注册内存清理处理器: {name}")
    
    def register_cache_manager(self, name: str, manager: Any):
        """注册缓存管理器"""
        self.cache_managers[name] = weakref.ref(manager)
        log.debug(f"注册缓存管理器: {name}")
    
    def force_cleanup(self) -> Dict[str, Any]:
        """强制执行内存清理"""
        start_usage = self.get_memory_usage()
        cleaned_items = {}
        
        log.warning(f"执行强制内存清理，当前内存使用: {start_usage['rss_mb']:.1f}MB ({start_usage['usage_percent']*100:.1f}%)")
        
        # 1. 执行垃圾回收
        collected = gc.collect()
        cleaned_items['gc_collected'] = collected
        
        # 2. 调用注册的清理函数
        for name, handler in list(self.cleanup_handlers.items()):
            try:
                result = handler()
                cleaned_items[f'handler_{name}'] = result
            except Exception as e:
                log.error(f"清理处理器 {name} 执行失败: {e}")
        
        # 3. 清理缓存管理器
        for name, manager_ref in list(self.cache_managers.items()):
            manager = manager_ref()
            if manager is None:
                # 弱引用失效，清理
                del self.cache_managers[name]
                continue
                
            try:
                if hasattr(manager, 'emergency_cleanup'):
                    result = manager.emergency_cleanup()
                    cleaned_items[f'cache_{name}'] = result
                elif hasattr(manager, 'clear_cache'):
                    manager.clear_cache()
                    cleaned_items[f'cache_{name}'] = 'cleared'
            except Exception as e:
                log.error(f"缓存管理器 {name} 清理失败: {e}")
        
        end_usage = self.get_memory_usage()
        freed_mb = start_usage['rss_mb'] - end_usage['rss_mb']
        
        self.cleanup_count += 1
        
        log.info(f"内存清理完成，释放 {freed_mb:.1f}MB，当前使用: {end_usage['rss_mb']:.1f}MB ({end_usage['usage_percent']*100:.1f}%)")
        
        return {
            'freed_mb': freed_mb,
            'before_mb': start_usage['rss_mb'],
            'after_mb': end_usage['rss_mb'],
            'cleaned_items': cleaned_items,
            'cleanup_count': self.cleanup_count
        }
    
    def _monitor_loop(self):
        """内存监控循环"""
        log.info("内存监控线程启动")
        
        while self.is_monitoring:
            try:
                current_time = time.time()
                
                # 定期检查内存使用
                if current_time - self.last_check_time >= self.config.check_interval:
                    usage = self.get_memory_usage()
                    self.last_check_time = current_time
                    
                    # 记录高内存使用
                    if usage['usage_percent'] > self.config.warning_threshold:
                        log.warning(f"内存使用较高: {usage['rss_mb']:.1f}MB ({usage['usage_percent']*100:.1f}%)")
                    
                    # 临界状态清理
                    elif usage['usage_percent'] > self.config.critical_threshold:
                        log.warning(f"内存使用达到临界状态，执行清理")
                        self.force_cleanup()
                
                # 定期垃圾回收
                if current_time - self.last_gc_time >= self.config.gc_interval:
                    collected = gc.collect()
                    self.last_gc_time = current_time
                    if collected > 0:
                        log.debug(f"定期垃圾回收完成，清理 {collected} 个对象")
                
                time.sleep(1)  # 每秒检查一次
                
            except Exception as e:
                log.error(f"内存监控循环出错: {e}")
                time.sleep(5)
    
    def start_monitoring(self):
        """开始内存监控"""
        if self.is_monitoring:
            return
        
        self.is_monitoring = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="MemoryMonitor",
            daemon=True
        )
        self.monitor_thread.start()
        log.info("内存监控已启动")
    
    def stop_monitoring(self):
        """停止内存监控"""
        if not self.is_monitoring:
            return
        
        self.is_monitoring = False
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        
        log.info("内存监控已停止")

# 全局内存管理器实例
memory_manager = MemoryManager()

# 清理状态跟踪（防止重复清理）
_last_cleanup_time = 0
_cleanup_in_progress = False
_cleanup_lock = Lock()


def get_memory_manager() -> MemoryManager:
    """获取全局内存管理器实例"""
    return memory_manager


def check_memory_limit() -> bool:
    """检查内存是否超限，如果超限则执行清理，但不阻止正常聊天"""
    if not MEMORY_MONITORING_ENABLED:
        return True
    
    global _last_cleanup_time, _cleanup_in_progress
    import time
    current_time = time.time()
    
    # 防止频繁清理（至少间隔30秒）
    if _cleanup_in_progress or (current_time - _last_cleanup_time < 30):
        return True
        
    manager = get_memory_manager()
    
    def _background_cleanup():
        """后台清理函数"""
        global _cleanup_in_progress, _last_cleanup_time
        
        with _cleanup_lock:
            if _cleanup_in_progress:
                return  # 已经有清理在进行
            _cleanup_in_progress = True
        
        try:
            manager.force_cleanup()
            _last_cleanup_time = time.time()
        except Exception as e:
            log.error(f"后台内存清理失败: {e}")
        finally:
            _cleanup_in_progress = False
    
    # 只在紧急状态下执行清理，但不阻止请求
    if manager.is_memory_emergency():
        log.warning("内存使用超过紧急阈值，执行后台清理（不影响当前请求）")
        try:
            cleanup_thread = threading.Thread(
                target=_background_cleanup,
                name="EmergencyMemoryCleanup",
                daemon=True
            )
            cleanup_thread.start()
        except Exception as e:
            log.error(f"启动后台内存清理失败: {e}")
    
    # 始终返回 True，不阻止请求
    return True


def register_memory_cleanup(name: str, handler: Callable):
    """注册内存清理处理器的便捷函数"""
    if MEMORY_MONITORING_ENABLED:
        memory_manager.register_cleanup_handler(name, handler)


def register_cache_for_cleanup(name: str, cache_manager: Any):
    """注册缓存管理器的便捷函数"""
    if MEMORY_MONITORING_ENABLED:
        memory_manager.register_cache_manager(name, cache_manager)


def get_memory_usage() -> Dict[str, float]:
    """获取物理内存使用情况的便捷函数"""
    if not MEMORY_MONITORING_ENABLED:
        return {'rss_mb': 0, 'usage_percent': 0, 'limit_mb': 100, 'available_mb': 100, 'peak_mb': 0}
    return memory_manager.get_memory_usage()


# 自动启动内存监控
def auto_start_monitoring():
    """自动启动内存监控"""
    if not MEMORY_MONITORING_ENABLED:
        log.info("内存监控功能已禁用 (AUTO_START_MEMORY_MONITOR=false)")
        return
        
    try:
        memory_manager.start_monitoring()
    except Exception as e:
        log.error(f"自动启动内存监控失败: {e}")


# 程序启动时自动启动监控
auto_start_monitoring()