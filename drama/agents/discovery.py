"""Discovery Agent — 选题评估

从公版语料库中评估作品的短剧改编潜力。
"""

import logging
import yaml
from pathlib import Path

from .base import BaseAgent

logger = logging.getLogger(__name__)


class DiscoveryAgent(BaseAgent):
    system_prompt_file = "discovery.md"

    def build_messages(self, context: dict) -> list[dict]:
        work = context["work"]          # 作品标题
        author = context.get("author", "未知")
        dynasty = context.get("dynasty", "未知")
        source = context.get("source", "")
        content = context["content"]     # 作品全文

        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": (
                f"作品：{work}\n"
                f"作者：{author}\n"
                f"朝代：{dynasty}\n"
                f"出处：{source}\n\n"
                f"原文：\n{content}\n\n"
                f"请评估这篇作品的短剧改编潜力，按指定 YAML 格式输出。"
            )},
        ]

    def parse_output(self, response: str, context: dict) -> dict:
        """解析 LLM 输出为结构化结果"""
        try:
            # 尝试提取 YAML 块
            yaml_text = response
            if "```yaml" in response:
                yaml_text = response.split("```yaml")[1].split("```")[0]
            elif "```" in response:
                yaml_text = response.split("```")[1].split("```")[0]

            report = yaml.safe_load(yaml_text)
            if report is None:
                report = {}
        except Exception as e:
            logger.warning(f"YAML 解析失败: {e}")
            report = {"raw_response": response, "parse_error": str(e)}

        report["work"] = context["work"]
        return report
