"""重试逻辑模块

提供重试装饰器和异步重试工具。

> 现状说明（勿误判为死代码）：本模块**当前无调用方是有意的**——占位 provider
> 不会失败、无需重试。它是**为真实 provider 接入预留**的：接即梦/可灵/真实 GLM
> 等会超时/限流/偶发失败的外部 API 时，在对应 executor 的 `_call_*` 上套
> `@retry(...)`（见 CLAUDE.md 开发约定"API 调用要有重试"）。接真实 provider
> 时统一接线，不要因为"暂无调用方"就删除。
"""

import logging
import time
from functools import wraps
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
):
    """同步重试装饰器

    Args:
        max_attempts: 最大尝试次数
        delay: 初始延迟（秒）
        backoff: 延迟倍增因子
        exceptions: 触发重试的异常类型
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            attempt = 1
            current_delay = delay
            while attempt <= max_attempts:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts:
                        logger.error(f"{func.__name__} 第{attempt}次重试失败（已达上限）: {e}")
                        raise
                    logger.warning(f"{func.__name__} 第{attempt}次失败，{current_delay:.1f}s后重试: {e}")
                    time.sleep(current_delay)
                    current_delay *= backoff
                    attempt += 1
        return wrapper
    return decorator


async def async_retry(
    func: Callable,
    *args,
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
    **kwargs,
):
    """异步重试函数

    Args:
        func: 异步函数
        max_attempts: 最大尝试次数
        delay: 初始延迟（秒）
        backoff: 延迟倍增因子
        exceptions: 触发重试的异常类型
    """
    import asyncio

    attempt = 1
    current_delay = delay
    while attempt <= max_attempts:
        try:
            return await func(*args, **kwargs)
        except exceptions as e:
            if attempt == max_attempts:
                logger.error(f"{func.__name__} 第{attempt}次重试失败（已达上限）: {e}")
                raise
            logger.warning(f"{func.__name__} 第{attempt}次失败，{current_delay:.1f}s后重试: {e}")
            await asyncio.sleep(current_delay)
            current_delay *= backoff
            attempt += 1
