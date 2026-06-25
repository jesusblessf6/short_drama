"""Agent 基类

所有创意层 Agent 继承 BaseAgent，统一接口：
- build_messages: 从上下文构建 LLM messages
- parse_output: 解析 LLM 输出为结构化结果
- run: 主入口（基类实现，子类一般不需要覆盖）
"""

import logging
from pathlib import Path
from typing import Any

from ..llm import LLMClient
from ..config import Config

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


class BaseAgent:
    """Agent 基类

    子类需要定义：
        system_prompt_file: str  — prompts/ 目录下的文件名
        build_messages(context) -> list[dict]
        parse_output(response, context) -> dict
    """

    system_prompt_file: str = ""

    def __init__(self, config: Config):
        self.config = config
        self.llm = LLMClient(config.llm)
        self.system_prompt = self._load_prompt()

    def _load_prompt(self) -> str:
        """从 prompts/ 目录加载 system prompt"""
        if not self.system_prompt_file:
            raise ValueError(f"{self.__class__.__name__} 未定义 system_prompt_file")
        path = PROMPTS_DIR / self.system_prompt_file
        if not path.exists():
            raise FileNotFoundError(f"Prompt 文件不存在: {path}")
        return path.read_text(encoding="utf-8")

    def run(self, context: dict) -> dict:
        """主入口：构建消息 → 调 LLM → 解析输出

        离线模式（llm.is_offline：显式 offline 或无 api_key）下走 offline_output，
        使整条管线零外部 key 即可端到端跑通。

        Args:
            context: 包含所有输入的上下文 dict

        Returns:
            包含输出和元数据的结果 dict（不含 status —— status 由 Orchestrator 决定）
        """
        if self.config.llm.is_offline:
            logger.info(f"{self.__class__.__name__}: LLM 离线模式，使用模板输出")
            result = self.offline_output(context)
            result.setdefault("cost_tokens", 0)
            return result

        messages = self.build_messages(context)
        logger.debug(f"{self.__class__.__name__} messages: {len(messages)} 条")

        response = self.llm.chat(messages)
        logger.debug(f"{self.__class__.__name__} response: {len(response)} 字符")

        result = self.parse_output(response, context)
        result["cost_tokens"] = self.llm.last_usage
        return result

    def build_messages(self, context: dict) -> list[dict]:
        """从上下文构建 LLM messages — 子类必须实现"""
        raise NotImplementedError

    def parse_output(self, response: str, context: dict) -> dict:
        """解析 LLM 输出为结构化结果 — 子类必须实现。

        约定：返回 dict **不含 `status` 字段**（status 由 Orchestrator 写入）。
        """
        raise NotImplementedError

    def offline_output(self, context: dict) -> dict:
        """离线模板输出 — 需要离线可跑的子类实现（生产 Agent 必须实现）"""
        raise NotImplementedError(
            f"{self.__class__.__name__} 未实现 offline_output，无法在离线模式运行"
        )
