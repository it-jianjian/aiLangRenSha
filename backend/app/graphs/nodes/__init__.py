"""节点级性能计时装饰器"""

import functools
import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


def timed_node(func: Callable) -> Callable:
    """装饰 async 节点函数，记录执行耗时

    输出格式: [Perf] {节点名} 耗时 {ms}ms
    """
    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        try:
            return await func(*args, **kwargs)
        finally:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.info(f"[Perf] {func.__name__} 耗时 {elapsed_ms}ms")
    return wrapper
