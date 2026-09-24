"""重试逻辑模块

提供重试装饰器和异步重试工具。

M1 扩展：错误分类。真实 provider 的异常在返回/抛出时标注类别，
调度器据此区分"值得重试"（网络抖动/超时/限流）与"重试也无用"
（鉴权失效/参数错误——立即进入 failed 终态，不浪费付费调用）。

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

# 错误类别（M1）。可重试类：瞬态故障，重试有意义；不可重试类：重试只会重复扣费。
ERROR_CLASSES = {"timeout", "rate_limit", "network", "auth", "param", "unknown"}
RETRYABLE_ERROR_CLASSES = {"timeout", "rate_limit", "network", "unknown"}
FATAL_ERROR_CLASSES = {"auth", "param"}


def classify_exception(e: BaseException) -> str:
    """把异常映射到错误类别。httpx 异常精确分类，其余字符串特征兜底。"""
    if isinstance(e, NotImplementedError):
        return "param"  # provider 未实现属于配置问题，重试无意义
    try:
        import httpx
        if isinstance(e, httpx.TimeoutException):
            return "timeout"
        if isinstance(e, httpx.HTTPStatusError):
            code = e.response.status_code
            if code == 429:
                return "rate_limit"
            if code in (401, 403):
                return "auth"
            if code in (400, 422):
                return "param"
            if code >= 500:
                return "network"  # 服务端瞬态错误，值得重试
            return "param"
        if isinstance(e, httpx.TransportError):
            return "network"
    except ImportError:
        pass
    text = str(e).lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "429" in text or "rate limit" in text or "rate_limit" in text or "限流" in text:
        return "rate_limit"
    if "401" in text or "403" in text or "unauthorized" in text or "鉴权" in text:
        return "auth"
    if "connection" in text or "network" in text or "connect" in type(e).__name__.lower():
        return "network"
    return "unknown"


def is_retryable(error_class: str) -> bool:
    """该错误类别是否值得自动重试"""
    return error_class in RETRYABLE_ERROR_CLASSES


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
):
    """同步重试装饰器（指数退避）

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
