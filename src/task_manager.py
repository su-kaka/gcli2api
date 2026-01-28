from src.i18n import ts
"""
Global task lifecycle management module
{ts("id_3607")}
"""

import asyncio
import weakref
from typing import Any, Dict, Set

from log import log


class TaskManager:
    f"""{ts("id_3608")} - {ts("id_3609")}"""

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
        self._resources: Set[Any] = set()  # {ts("id_3610")}
        self._shutdown_event = asyncio.Event()
        self._initialized = True
        log.debug("TaskManager initialized")

    def register_task(self, task: asyncio.Task, description: str = None) -> asyncio.Task:
        f"""{ts("id_3611")}"""
        self._tasks.add(task)
        task.add_done_callback(lambda t: self._tasks.discard(t))

        if description:
            task.set_name(description)

        log.debug(f"Registered task: {task.get_name() or 'unnamed'}")
        return task

    def create_task(self, coro, *, name: str = None) -> asyncio.Task:
        f"""{ts("id_3612")}"""
        task = asyncio.create_task(coro, name=name)
        return self.register_task(task, name)

    def register_resource(self, resource: Any) -> Any:
        f"""{ts("id_3613")}HTTP{ts("id_3614")}"""
        # {ts("id_3615")}
        self._resources.add(weakref.ref(resource))
        log.debug(f"Registered resource: {type(resource).__name__}")
        return resource

    async def shutdown(self, timeout: float = 30.0):
        f"""{ts("id_3616")}"""
        log.info("TaskManager shutdown initiated")

        # {ts("id_3617")}
        self._shutdown_event.set()

        # {ts("id_3618")}
        cancelled_count = 0
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
                cancelled_count += 1

        if cancelled_count > 0:
            log.info(f"Cancelled {cancelled_count} pending tasks")

        # {ts("id_3619")}
        if self._tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks, return_exceptions=True), timeout=timeout
                )
            except asyncio.TimeoutError:
                log.warning(f"Some tasks did not complete within {timeout}s timeout")

        # {ts("id_2942")} - {ts("id_3620")}
        cleaned_resources = 0
        failed_resources = 0
        for resource_ref in list(self._resources):
            resource = resource_ref()
            if resource is not None:
                try:
                    if hasattr(resource, "close"):
                        if asyncio.iscoroutinefunction(resource.close):
                            await resource.close()
                        else:
                            resource.close()
                    elif hasattr(resource, "aclose"):
                        await resource.aclose()
                    cleaned_resources += 1
                except Exception as e:
                    log.warning(f"Failed to close resource {type(resource).__name__}: {e}")
                    failed_resources += 1
            # {ts("id_3621")}

        if cleaned_resources > 0:
            log.info(f"Cleaned up {cleaned_resources} resources")
        if failed_resources > 0:
            log.warning(f"Failed to clean {failed_resources} resources")

        self._tasks.clear()
        self._resources.clear()
        log.info("TaskManager shutdown completed")

    @property
    def is_shutdown(self) -> bool:
        f"""{ts("id_3622")}"""
        return self._shutdown_event.is_set()

    def get_stats(self) -> Dict[str, int]:
        f"""{ts("id_3623")}"""
        return {
            "active_tasks": len(self._tasks),
            "registered_resources": len(self._resources),
            "is_shutdown": self.is_shutdown,
        }


# {ts("id_3624")}
task_manager = TaskManager()


def create_managed_task(coro, *, name: str = None) -> asyncio.Task:
    f"""{ts("id_3625")}"""
    return task_manager.create_task(coro, name=name)


def register_resource(resource: Any) -> Any:
    f"""{ts("id_3626")}"""
    return task_manager.register_resource(resource)


async def shutdown_all_tasks(timeout: float = 30.0):
    f"""{ts("id_3627")}"""
    await task_manager.shutdown(timeout)
