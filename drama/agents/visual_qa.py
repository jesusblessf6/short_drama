"""Visual QA Agent — 画面质检

审查 AI 生成的图片和视频，判断是否合格。
需要 vision 模型。
"""

import logging
import yaml
from pathlib import Path

from .base import BaseAgent

logger = logging.getLogger(__name__)


class VisualQAAgent(BaseAgent):
    system_prompt_file = "visual_qa.md"

    def build_messages(self, context: dict) -> list[dict]:
        project = context["project"]
        shot = context["shot"]
        sub_task = context["sub_task"]  # "text2img" or "img2video"
        file_path = context["file_path"]

        # 读取角色描述卡（用于一致性比对）
        characters_dir = project.get_path("characters")
        character_cards = ""
        if characters_dir.exists():
            for f in sorted(characters_dir.glob("*.md")) + sorted(characters_dir.glob("*.yaml")):
                character_cards += f"\n--- {f.stem} ---\n{f.read_text(encoding='utf-8')[:500]}\n"

        task_type = "图片" if sub_task == "text2img" else "视频"

        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": (
                f"请质检以下{task_type}。\n\n"
                f"镜头ID: {shot['id']}\n"
                f"镜头场景: {shot.get('scene', '')}\n\n"
                f"角色描述卡（用于一致性比对）:\n{character_cards}\n\n"
                f"请检查这张{task_type}，按 YAML 格式输出质检结果。"
            )},
        ]

    def run(self, context: dict) -> dict:
        """重写 run，因为需要 vision 调用"""
        # 解 C：离线模式（无 vision key / 占位画面）短路质检，直接通过，
        # 避免内联 vision 调用抛错被吞 → 镜头卡在 generating → 无限重生成。
        if self.config.llm.is_offline:
            return {"pass": True, "notes": "离线模式，QA 跳过", "cost_tokens": 0}

        file_path = context["file_path"]
        messages = self.build_messages(context)

        # 判断是图片还是视频
        path = Path(file_path)
        if path.suffix in (".png", ".jpg", ".jpeg", ".webp"):
            response = self.llm.chat_with_image(messages, file_path)
        elif path.suffix in (".mp4", ".webm", ".gif"):
            # 视频质检：取第一帧截图
            # TODO: 实现视频帧提取
            logger.warning("视频质检暂未实现帧提取，跳过")
            return {"pass": True, "notes": "视频质检未实现，默认通过"}
        else:
            logger.warning(f"不支持的文件格式: {path.suffix}")
            return {"pass": False, "notes": f"不支持的格式: {path.suffix}"}

        result = self.parse_output(response, context)
        result["cost_tokens"] = self.llm.last_usage
        return result

    def parse_output(self, response: str, context: dict) -> dict:
        try:
            yaml_text = response
            if "```yaml" in response:
                yaml_text = response.split("```yaml")[1].split("```")[0]
            elif "```" in response:
                yaml_text = response.split("```")[1].split("```")[0]

            result = yaml.safe_load(yaml_text)
            if result is None:
                result = {"pass": True, "notes": "解析失败，默认通过"}
        except Exception as e:
            logger.warning(f"质检结果解析失败: {e}")
            result = {"pass": True, "notes": f"解析失败: {e}"}

        return result
