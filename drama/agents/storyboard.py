"""Storyboard Agent — 分镜设计

将剧本拆解为镜头级分镜脚本，生成文生图和图生视频提示词。
"""

import logging
import re
from pathlib import Path

from .base import BaseAgent

logger = logging.getLogger(__name__)


class StoryboardAgent(BaseAgent):
    system_prompt_file = "storyboard.md"

    def build_messages(self, context: dict) -> list[dict]:
        project = context["project"]
        state = context["state"]
        episode = state["episode"]

        # 读取剧本
        script_file = state["script"].get("file")
        script_content = ""
        if script_file:
            path = project.project_root / script_file
            if path.exists():
                script_content = path.read_text(encoding="utf-8")

        # 读取角色描述卡
        characters_dir = project.get_path("characters")
        character_cards = ""
        if characters_dir.exists():
            for f in sorted(characters_dir.glob("*.md")) + sorted(characters_dir.glob("*.yaml")):
                character_cards += f"\n\n--- {f.stem} ---\n{f.read_text(encoding='utf-8')}"

        # 读取风格定调
        style_dir = project.get_path("art") / "风格定调"
        style_guide = ""
        if style_dir.exists():
            for f in sorted(style_dir.glob("*.md")):
                style_guide += f.read_text(encoding="utf-8") + "\n"

        user_msg = (
            f"请为第 {state.get('episode_num', '?')} 集制作分镜脚本。\n\n"
            f"## 剧本\n{script_content}\n\n"
        )
        if character_cards:
            user_msg += f"## 角色描述卡\n{character_cards}\n\n"
        if style_guide:
            user_msg += f"## 风格定调\n{style_guide}\n\n"

        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_msg},
        ]

    NEGATIVE_DEFAULT = "毁容，面部扭曲，肢体变形，多余手指，模糊"

    def parse_output(self, response: str, context: dict) -> dict:
        project = context["project"]
        state = context["state"]
        episode = state["episode"]
        act = state.get("act", "")

        # 写入分镜脚本文件
        sb_dir = project.get_path("storyboards") / act
        sb_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{episode}_storyboard.md"
        file_path = sb_dir / file_name
        file_path.write_text(response, encoding="utf-8")

        # 解析结构化镜头（解 A：executor 需要每镜头的 prompt）
        shots = self._parse_shots(response, episode)
        if not shots:
            # 解析失败兜底：至少产 1 个镜头，避免整集无镜头卡死
            logger.warning(f"{episode} 分镜解析未得到镜头，使用兜底镜头")
            shots = [self._fallback_shot(episode, 1, "未解析场景")]

        return {
            "file": str(file_path.relative_to(project.project_root)),
            "shot_count": len(shots),
            "shots": shots,
        }

    def _parse_shots(self, response: str, episode: str) -> list[dict]:
        """从分镜 markdown 的「### 镜头NN」详情段解析结构化镜头"""
        blocks = re.split(r"###\s*镜头\s*\d+", response)[1:]  # 丢掉首段（表格/标题）
        shots = []
        for i, block in enumerate(blocks, start=1):
            def field(name: str) -> str:
                m = re.search(rf"[-*]?\s*{name}[：:]\s*(.+)", block)
                return m.group(1).strip() if m else ""

            scene = field("场景")
            t2i = field("文生图Prompt") or field("文生图")
            i2v = field("图生视频Prompt") or field("图生视频")
            neg = field("负面提示词") or self.NEGATIVE_DEFAULT
            speaker, dialogue = self._split_speaker(field("台词"))
            jingbie = field("景别")
            shot_type = self._infer_type(i2v, jingbie)
            shots.append({
                "id": f"{episode}_shot{i:02d}",
                "scene": scene or "场景",
                "type": shot_type,
                "t2i_prompt": t2i or f"{jingbie or '中景'}，{scene}",
                "i2v_prompt": i2v or f"{jingbie or '中景'}固定镜头，缓慢推进",
                "negative_prompt": neg,
                "dialogue": dialogue,
                "speaker": speaker,
                "duration": 4,
            })
        return shots

    @staticmethod
    def _split_speaker(text: str) -> tuple[str, str]:
        """把「说话人：台词」切成 (speaker, text);无说话人则归旁白"""
        t = (text or "").strip()
        if not t:
            return "旁白", ""
        m = re.match(r"^([^（）：:]{1,8})[：:](.+)$", t)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return "旁白", t

    @staticmethod
    def _infer_type(i2v_prompt: str, jingbie: str) -> str:
        """据动态描述粗略判定镜头类型，决定重试预算"""
        text = i2v_prompt
        if any(k in text for k in ("打斗", "舞蹈", "奔跑", "翻", "激烈")):
            return "complex"
        if any(k in text for k in ("说", "口型", "对话")):
            return "lip_sync"
        if any(k in text for k in ("走", "坐", "看", "转身", "推进", "拨")):
            return "simple"
        return "static"

    def _fallback_shot(self, episode: str, idx: int, scene: str) -> dict:
        return {
            "id": f"{episode}_shot{idx:02d}",
            "scene": scene,
            "type": "static",
            "t2i_prompt": f"中景，{scene}，古风写实",
            "i2v_prompt": "中景固定镜头，缓慢推进",
            "negative_prompt": self.NEGATIVE_DEFAULT,
            "dialogue": "",
            "speaker": "旁白",
            "duration": 4,
        }

    def offline_output(self, context: dict) -> dict:
        """离线模板分镜：基于剧本生成 3 个结构化镜头（含 7段式/5段式 prompt 与台词）"""
        state = context["state"]
        episode = state["episode"]

        # 尝试从剧本里抽取台词，丰富占位画面与配音
        dialogues = self._read_script_dialogues(context)

        specs = [
            ("商府内堂", "中景，商三官（鹅蛋脸细长眉左眉尾小痣月白襦裙），跪于父亲灵前，烛光摇曳的内堂，悲恸压抑，古风写实工笔，暖黄烛光侧光",
             "中景固定镜头，缓慢推进，商三官低头攥拳，烛火轻晃，背景幽暗", "simple"),
            ("庭院夜色", "全景，商三官（月白襦裙）独立庭中，青砖庭院明月当空，孤寂决绝，古风写实，冷青月光",
             "全景固定镜头，极缓推进，三官抬头望月，云影掠过明月", "static"),
            ("灵堂火盆", "特写，商三官（细长眉眼尾微翘）手持长发立于火盆前，火光映面，决绝，古风写实，暖橙火光",
             "特写固定镜头，缓慢推进，三官松手长发落入火盆，火星升腾", "simple"),
        ]
        shots = []
        for i, (scene, t2i, i2v, stype) in enumerate(specs, start=1):
            dlg = dialogues[i - 1] if i - 1 < len(dialogues) else {"speaker": "旁白", "text": ""}
            shots.append({
                "id": f"{episode}_shot{i:02d}",
                "scene": scene,
                "type": stype,
                "t2i_prompt": t2i,
                "i2v_prompt": i2v,
                "negative_prompt": self.NEGATIVE_DEFAULT,
                "dialogue": dlg["text"],
                "speaker": dlg["speaker"],
                "duration": 4,
            })

        # 写一份可读的分镜 md
        lines = [f"# {episode} 分镜脚本（离线模板）\n"]
        for i, s in enumerate(shots, start=1):
            lines.append(f"### 镜头{i:02d}")
            lines.append(f"- 场景：{s['scene']}")
            lines.append(f"- 文生图Prompt：{s['t2i_prompt']}")
            lines.append(f"- 图生视频Prompt：{s['i2v_prompt']}")
            lines.append(f"- 负面提示词：{s['negative_prompt']}")
            dlg_line = f"{s['speaker']}：{s['dialogue']}" if s['dialogue'] else "（无台词）"
            lines.append(f"- 台词：{dlg_line}\n")
        response = "\n".join(lines)

        project = context["project"]
        act = state.get("act", "")
        sb_dir = project.get_path("storyboards") / act
        sb_dir.mkdir(parents=True, exist_ok=True)
        file_path = sb_dir / f"{episode}_storyboard.md"
        file_path.write_text(response, encoding="utf-8")

        return {
            "file": str(file_path.relative_to(project.project_root)),
            "shot_count": len(shots),
            "shots": shots,
        }

    def _read_script_dialogues(self, context: dict) -> list[dict]:
        """从剧本 md 抽取台词（保留说话人），形如「角色：台词」，供镜头/配音使用"""
        project = context["project"]
        state = context["state"]
        script_file = state.get("script", {}).get("file")
        if not script_file:
            return []
        path = project.project_root / script_file
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        dialogues = []
        for line in text.splitlines():
            line = line.strip()
            m = re.match(r"^([^（）\s#\-*][^：:]{0,8})[：:](.+)$", line)
            if m and not line.startswith("-"):
                dialogues.append({"speaker": m.group(1).strip(), "text": m.group(2).strip()})
        return dialogues
