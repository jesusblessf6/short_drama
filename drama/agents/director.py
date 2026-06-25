"""Director Agent — 导演终审

单集成片终审 + 处理生产升级问题。
"""

import logging
import yaml
from pathlib import Path

from .base import BaseAgent

logger = logging.getLogger(__name__)


class DirectorAgent(BaseAgent):
    system_prompt_file = "director.md"

    def build_messages(self, context: dict) -> list[dict]:
        project = context["project"]
        state = context["state"]
        episode = state["episode"]
        extra = context.get("extra", {})

        # 判断是终审还是升级处理
        if extra and extra.get("reason"):
            return self._build_escalation_messages(context, extra)
        else:
            return self._build_review_messages(context)

    def _build_review_messages(self, context: dict) -> list[dict]:
        """构建终审 messages"""
        project = context["project"]
        state = context["state"]

        # 读取剧本
        script_content = ""
        script_file = state["script"].get("file")
        if script_file:
            path = project.project_root / script_file
            if path.exists():
                script_content = path.read_text(encoding="utf-8")

        # 读取分镜
        storyboard_content = ""
        sb_file = state["storyboard"].get("file")
        if sb_file:
            path = project.project_root / sb_file
            if path.exists():
                storyboard_content = path.read_text(encoding="utf-8")[:3000]

        # 成片路径
        composite_file = state["composite"].get("file", "")

        # 镜头统计
        shots = state.get("shots", [])
        shot_summary = f"总镜头数: {len(shots)}, 通过: {sum(1 for s in shots if s['img2video']['status'] == 'approved')}"

        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": (
                f"请终审第 {state.get('episode_num', '?')} 集。\n\n"
                f"## 镜头统计\n{shot_summary}\n\n"
                f"## 剧本\n{script_content[:3000]}\n\n"
                f"## 分镜概要\n{storyboard_content}\n\n"
                f"## 成片\n{composite_file}\n\n"
                f"请按 YAML 格式输出终审结果。"
            )},
        ]

    def _build_escalation_messages(self, context: dict, extra: dict) -> list[dict]:
        """构建升级处理 messages"""
        state = context["state"]
        shot = context.get("shot", {})

        reason = extra.get("reason", "")
        shot_id = shot.get("id", "")

        # 读取分镜脚本中该镜头的描述
        sb_file = state["storyboard"].get("file")
        shot_desc = ""
        if sb_file:
            path = context["project"].project_root / sb_file
            if path.exists():
                content = path.read_text(encoding="utf-8")
                # 尝试提取该镜头的描述
                import re
                pattern = rf"###.*{shot_id}.*?\n(.*?)(?=###|$)"
                match = re.search(pattern, content, re.DOTALL)
                if match:
                    shot_desc = match.group(1)[:1000]

        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": (
                f"镜头 {shot_id} 生产超过重试上限，需要你的决策。\n\n"
                f"升级原因: {reason}\n"
                f"镜头描述:\n{shot_desc}\n\n"
                f"请决定如何处理（simplify/downgrade/skip/manual），按 YAML 格式输出。"
            )},
        ]

    def parse_output(self, response: str, context: dict) -> dict:
        extra = context.get("extra", {})
        is_escalation = extra and extra.get("reason")

        try:
            yaml_text = response
            if "```yaml" in response:
                yaml_text = response.split("```yaml")[1].split("```")[0]
            elif "```" in response:
                yaml_text = response.split("```")[1].split("```")[0]

            result = yaml.safe_load(yaml_text)
            if result is None:
                result = {}
        except Exception as e:
            logger.warning(f"导演输出解析失败: {e}")
            result = {"approved": False, "overall_notes": f"解析失败: {e}", "raw": response}

        if is_escalation:
            result["approved"] = False  # 升级处理不是终审
        else:
            result.setdefault("approved", False)

        return result

    def offline_output(self, context: dict) -> dict:
        """离线模板：终审直接通过；升级处理直接降级（保证集能收敛不卡死）"""
        extra = context.get("extra", {})
        if extra and extra.get("reason"):
            return {
                "approved": False,
                "escalation_resolution": "downgrade",
                "action": "降级为静态图 + 旁白（离线模板决策）",
                "reason": f"离线模板：{extra.get('reason')}",
            }
        return {
            "approved": True,
            "score": 8,
            "overall_notes": "离线模板终审通过",
        }
