"""Storyboard Agent — 分镜设计

将剧本拆解为镜头级分镜脚本，生成文生图和图生视频提示词。
"""

import logging
import re
from pathlib import Path

from .base import BaseAgent
from .validation import character_names, repair_shots, validate_shots

logger = logging.getLogger(__name__)


def _clean_line(text: str) -> str:
    """台词清洗：剥离表演提示（（叩板）/（轻声））、引号与舞台说明。

    台词要进 TTS 与字幕——「（叩板）"原来姹紫嫣红开遍"」必须只剩可朗读的正文。
    """
    t = re.sub(r"[（(][^）)]*[）)]", "", text or "")   # 表演提示
    t = t.strip().strip("\"'“”「」『』")                  # 引号
    t = re.sub(r"\s+", " ", t).strip()
    return t


class StoryboardAgent(BaseAgent):
    system_prompt_file = "storyboard.md"

    def run(self, context: dict) -> dict:
        """重写 run：真实路径带"结构化校验 + 格式修复循环"（M2-2）。

        级联：裸输出校验（空 prompt 视为硬伤，不做掩蔽）→ 有硬伤且未达上限时
        把错误清单喂回 LLM 自修复 → 达上限后机械修复兜底（repair_shots）→
        仍硬伤则抛错，绝不带病进入付费生成。离线模板输出同样过校验作保险。
        """
        if self.config.llm.is_offline:
            result = self.offline_output(context)
            result.setdefault("cost_tokens", 0)
            result["shots"] = repair_shots(result["shots"],
                                           context["state"]["episode"])
            fatal, _ = validate_shots(result["shots"])
            if fatal:
                raise ValueError(f"离线分镜模板输出存在硬伤: {fatal}")
            return result

        project = context["project"]
        state = context["state"]
        episode = state["episode"]
        max_repairs = int(project.production.get("max_format_repairs", 2))
        characters = character_names(project)
        messages = self.build_messages(context)

        for attempt in range(max_repairs + 1):
            response = self.llm.chat(messages)
            result = self.parse_output(response, context)
            fatal, warns = validate_shots(result["shots"], characters)
            for w in warns:
                logger.warning(f"[{episode}] 分镜校验警告: {w}")
            if not fatal:
                result["shots"] = repair_shots(result["shots"], episode)
                result["cost_tokens"] = self.llm.last_usage
                return result
            if attempt < max_repairs:
                logger.warning(
                    f"[{episode}] 分镜格式问题（第{attempt + 1}次修复）: {fatal}")
                messages = messages + [
                    {"role": "assistant", "content": response},
                    {"role": "user", "content":
                        "你上次的分镜输出存在以下格式问题，请修正后重新输出**完整**分镜"
                        "（保持原有格式要求，不要解释）：\n"
                        + "\n".join(f"- {e}" for e in fatal)},
                ]

        # 修复次数耗尽：机械修复做最后兜底（能救则救），仍硬伤才拒绝
        repaired = repair_shots(result["shots"], episode)
        fatal2, _ = validate_shots(repaired, characters)
        if fatal2:
            raise ValueError(
                f"{episode} 分镜格式修复 {max_repairs} 次后仍有硬伤，"
                f"拒绝进入生成: {fatal2}")
        logger.warning(f"[{episode}] 格式修复次数耗尽，机械修复兜底通过")
        result["shots"] = repaired
        result["cost_tokens"] = self.llm.last_usage
        return result

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
        """从分镜 markdown 解析结构化镜头。

        两种形态（真实 LLM 会在两者间漂移，解析器必须都认）：
        1. `### 镜头NN` 块格式（prompt 要求的规范格式）
        2. markdown 表格（模型自行改用时）——从表格行解析，台词/备注列兜底取台词
        """
        shots = self._parse_shot_blocks(response, episode)
        if shots:
            return shots
        table = self._parse_shot_table(response, episode)
        if table:
            logger.warning("分镜为表格格式（非规范块格式），已按表格解析 %d 个镜头",
                           len(table))
        return table

    def _parse_shot_blocks(self, response: str, episode: str) -> list[dict]:
        """规范形态：`### 镜头NN` 详情块"""
        blocks = re.split(r"###\s*镜头\s*\d+", response)[1:]  # 丢掉首段（表格/标题）
        shots = []
        for i, block in enumerate(blocks, start=1):
            def field(name: str) -> str:
                m = re.search(rf"[-*]?\s*{name}[：:]\s*(.+)", block)
                return m.group(1).strip() if m else ""

            scene = field("场景")
            t2i = field("文生图Prompt") or field("文生图")
            i2v = field("图生视频Prompt") or field("图生视频")
            # 注意：prompt 缺失不做掩蔽兜底（裸值交给校验/修复循环处理，
            # 否则 LLM 的格式硬伤永远到不了修复循环）
            neg = field("负面提示词") or self.NEGATIVE_DEFAULT
            speaker, dialogue = self._split_speaker(field("台词"))
            jingbie = field("景别")
            shot_type = self._infer_type(i2v, jingbie)
            shots.append({
                "id": f"{episode}_shot{i:02d}",
                "scene": scene or "场景",
                "type": shot_type,
                "t2i_prompt": t2i,
                "i2v_prompt": i2v,
                "negative_prompt": neg,
                "dialogue": dialogue,
                "speaker": speaker,
                "duration": self._parse_duration(field("时长")),
            })
        return shots

    # 表格列名 → 语义（模型可能用各种叫法，按包含关系匹配）
    _TABLE_COLS = {
        "shot": ("镜头", "序号"),
        "scene": ("场景",),
        "t2i": ("文生图", "t2i"),
        "i2v": ("图生视频", "i2v"),
        "dialogue": ("台词", "备注", "对白"),
    }

    def _parse_shot_table(self, response: str, episode: str) -> list[dict]:
        """兜底形态：markdown 表格。台词在独立列或与备注混在一格，都尝试取。"""
        lines = [ln for ln in response.splitlines() if ln.strip().startswith("|")]
        if len(lines) < 2:
            return []
        header = [c.strip() for c in lines[0].strip().strip("|").split("|")]
        idx: dict[str, int] = {}
        for key, names in self._TABLE_COLS.items():
            for i, h in enumerate(header):
                if any(n in h for n in names):
                    idx.setdefault(key, i)
                    break
        if "t2i" not in idx or "i2v" not in idx:
            return []   # 不是分镜表
        shots = []
        for row in lines[1:]:
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            if len(cells) <= idx["t2i"]:
                continue
            t2i, i2v = cells[idx["t2i"]], cells[idx["i2v"]]
            if not t2i and not i2v:
                continue
            if set("".join(cells)) <= set("-: "):   # 表格分隔行（---|---）
                continue
            dlg_cell = cells[idx["dialogue"]] if "dialogue" in idx and idx["dialogue"] < len(cells) else ""
            speaker, dialogue = self._split_table_dialogue(dlg_cell)
            i = len(shots) + 1
            shots.append({
                "id": f"{episode}_shot{i:02d}",
                "scene": cells[idx["scene"]] if "scene" in idx and idx["scene"] < len(cells) else "场景",
                "type": self._infer_type(i2v, ""),
                "t2i_prompt": t2i,
                "i2v_prompt": i2v,
                "negative_prompt": self.NEGATIVE_DEFAULT,
                "dialogue": dialogue,
                "speaker": speaker,
                "duration": 5,
            })
        return shots

    def _split_table_dialogue(self, cell: str) -> tuple[str, str]:
        """表格台词格：必须严格是「角色名：台词内容」才算台词。

        模型常把台词与拍摄备注混在同一列（"磨石霍霍有声"是音效备注、"商士禹台词镜头"
        是说明），故要求有「角色：」前缀且内容非空——否则视为备注，不进配音/字幕。
        多角色用 ／ 分隔时取第一位。
        """
        text = (cell or "").strip()
        if not text or any(k in text for k in ("无台词", "无对白", "音效：", "环境音")):
            return "旁白", ""
        first = re.split(r"[／/]", text)[0].strip()
        m = re.match(r"^([^（()：:]{1,8})[：:]\s*(.+)$", first)
        if not m:
            return "旁白", ""          # 无「角色：」前缀 → 备注，不算台词
        speaker, content = m.group(1).strip(), m.group(2).strip()
        content = re.sub(r"^[（(][^）)]*[）)]\s*", "", content).strip("\"'“”「」")
        return (speaker, content) if content else ("旁白", "")

    @staticmethod
    def _parse_duration(text: str) -> int:
        """「6秒」/「6s」→ 6；解析不出回 4（后续 repair_shots 会钳制）"""
        m = re.search(r"(\d+)", text or "")
        if m:
            return int(m.group(1))
        return 4

    @staticmethod
    def _split_speaker(text: str) -> tuple[str, str]:
        """把「说话人：台词」切成 (speaker, text);无说话人则归旁白。

        占位词（无台词/无对白/音效：…）一律视为空——否则会把"无台词"三个字
        送进配音与字幕（真实踩过）。
        """
        t = (text or "").strip()
        if not t or any(k in t for k in ("无台词", "无对白", "音效：", "环境音")):
            return "旁白", ""
        m = re.match(r"^([^（）：:]{1,8})[：:](.+)$", t)
        if m:
            return m.group(1).strip(), _clean_line(m.group(2))
        return "旁白", _clean_line(t)

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
