"""Executor 基类

所有执行层 Executor 继承 BaseExecutor。
Executor 是纯 API 调用 + 重试逻辑，不涉及 LLM。

结果 dict 契约（M1 扩展）：
    success: bool            必有
    error: str               失败时必有
    error_class: str         失败时尽量提供（timeout/rate_limit/network/auth/param/unknown），
                             调度器据此类别决定重试还是直接 failed 终态
    file: str                成功时产物路径
    cost: float              成功时实际/预估费用（未知价格不得返回 0 冒充免费——启动校验兜底）
    source: str              产物来源 provider（placeholder/jimeng/kling/edge_tts/...），
                             写入状态作产物溯源（占位作废、正式模式拦截的依据）
    degraded: bool           降级产物（如静音轨）必须显式标记；
                             正式模式下调度器拒绝其自动通过
    external_task_id: str    提交成功但未及完成的异步任务 ID（恢复轮询凭据）
"""

import logging
from typing import Any

from ..config import Config
from ..utils.retry import classify_exception

logger = logging.getLogger(__name__)


class BaseExecutor:
    """Executor 基类

    子类需要实现：
        run(task) -> dict
        validate_input(task) -> bool
    """

    def __init__(self, config: Config):
        self.config = config

    @staticmethod
    def fail(e: BaseException, **extra) -> dict:
        """统一的失败返回：附带错误类别，供调度器区分可否重试"""
        return {"success": False, "error": str(e),
                "error_class": classify_exception(e), **extra}

    def run(self, task: dict) -> dict:
        """主入口 — 子类必须实现"""
        raise NotImplementedError

    def validate_input(self, task: dict) -> bool:
        """检查输入是否完整 — 子类必须实现"""
        raise NotImplementedError
