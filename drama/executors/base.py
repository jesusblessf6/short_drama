"""Executor 基类

所有执行层 Executor 继承 BaseExecutor。
Executor 是纯 API 调用 + 重试逻辑，不涉及 LLM。
"""

import logging
from typing import Any

from ..config import Config

logger = logging.getLogger(__name__)


class BaseExecutor:
    """Executor 基类

    子类需要实现：
        run(task) -> dict
        validate_input(task) -> bool
    """

    def __init__(self, config: Config):
        self.config = config

    def run(self, task: dict) -> dict:
        """主入口 — 子类必须实现"""
        raise NotImplementedError

    def validate_input(self, task: dict) -> bool:
        """检查输入是否完整 — 子类必须实现"""
        raise NotImplementedError
