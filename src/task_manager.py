"""
Global task lifecycle management module
管理应用程序中所有异步任务的生命周期，确保正确清理
"""
import asyncio
import weakref
from typing import Set, Dict, Any
from log import log


class TaskManager:
    """全局异步任务管理器 - 单例模式"""
    
    _instance = None
    _lock = asyncio.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._tasks: Set[asyncio.Task] = set()
        self._resources: Set[Any] = set()  # 需要关闭的资源
        self._shutdown_event = asyncio.Event()
        self._initialized = True
        log.debug("TaskManager initialized")
    
    def register_task(self, task: asyncio.Task, description: str = None) -> asyncio.Task:
        """注册一个任务供生命周期管理"""
        self._tasks.add(task)
        task.add_done_callback(lambda t: self._tasks.discard(t))
        
        if description:
            task.set_name(description)
        
        log.debug(f"Registered task: {task.get_name() or 'unnamed'}")
        return task
    
    def create_task(self, coro, *, name: str = None) -> asyncio.Task:
        """创建并注册一个任务"""
        task = asyncio.create_task(coro, name=name)
        return self.register_task(task, name)
    
    def register_resource(self, resource: Any) -> Any:
        """注册一个需要清理的资源（如HTTP客户端、文件句柄等）"""
        # 使用弱引用避免循环引用
        self._resources.add(weakref.ref(resource))
        log.debug(f"Registered resource: {type(resource).__name__}")
        return resource
    
    async def shutdown(self, timeout: float = 30.0):
        """关闭所有任务和资源"""
        log.info("TaskManager shutdown initiated")
        
        # 设置关闭标志
        self._shutdown_event.set()
        
        # 取消所有未完成的任务
        cancelled_count = 0
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
                cancelled_count += 1
        
        if cancelled_count > 0:
            log.info(f"Cancelled {cancelled_count} pending tasks")
        
        # 等待所有任务完成（包括取消）
        if self._tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks, return_exceptions=True),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                log.warning(f"Some tasks did not complete within {timeout}s timeout")
        
        # 清理资源
        cleaned_resources = 0
        for resource_ref in list(self._resources):
            resource = resource_ref()
            if resource is not None:
                try:
                    if hasattr(resource, 'close'):
                        if asyncio.iscoroutinefunction(resource.close):
                            await resource.close()
                        else:
                            resource.close()
                    elif hasattr(resource, 'aclose'):
                        await resource.aclose()
                    cleaned_resources += 1
                except Exception as e:
                    log.warning(f"Failed to close resource {type(resource).__name__}: {e}")
        
        if cleaned_resources > 0:
            log.info(f"Cleaned up {cleaned_resources} resources")
        
        self._tasks.clear()
        self._resources.clear()
        log.info("TaskManager shutdown completed")
    
    @property
    def is_shutdown(self) -> bool:
        """检查是否已经开始关闭"""
        return self._shutdown_event.is_set()
    
    def get_stats(self) -> Dict[str, int]:
        """获取任务管理统计信息"""
        return {
            'active_tasks': len(self._tasks),
            'registered_resources': len(self._resources),
            'is_shutdown': self.is_shutdown
        }


# 全局任务管理器实例
task_manager = TaskManager()


def create_managed_task(coro, *, name: str = None) -> asyncio.Task:
    """创建一个被管理的异步任务的便捷函数"""
    return task_manager.create_task(coro, name=name)


def register_resource(resource: Any) -> Any:
    """注册资源的便捷函数"""
    return task_manager.register_resource(resource)


async def shutdown_all_tasks(timeout: float = 30.0):
    """关闭所有任务的便捷函数"""
    await task_manager.shutdown(timeout)