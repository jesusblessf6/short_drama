"""Writer Agent — 编剧

根据框架、人物设定和原著，撰写单集剧本。
"""

import logging
from pathlib import Path

from .base import BaseAgent

logger = logging.getLogger(__name__)


class WriterAgent(BaseAgent):
    system_prompt_file = "writer.md"

    def build_messages(self, context: dict) -> list[dict]:
        project = context["project"]
        episode_num = context.get("episode_num")
        state = context.get("state", {})

        # 从项目目录读取框架
        framework_path = project.get_path("framework")
        framework = framework_path.read_text(encoding="utf-8") if framework_path.exists() else ""

        # 读取人物目录
        characters_dir = project.get_path("characters")
        characters = ""
        if characters_dir.exists():
            for f in sorted(characters_dir.glob("*.md")):
                characters += f"\n\n--- {f.stem} ---\n{f.read_text(encoding='utf-8')}"

        # 读取原著参考（如果有）
        ref_dir = project.get_path("reference")
        source_text = ""
        if ref_dir.exists():
            for f in sorted(ref_dir.glob("*.md")):
                source_text += f.read_text(encoding="utf-8") + "\n"

        # 获取幕名
        act = state.get("act", "")

        # 读取已有剧本（前集内容，保持连续性）
        scripts_dir = project.get_path("scripts")
        prev_script = ""
        if scripts_dir.exists():
            prev_ep = episode_num - 1 if episode_num else 0
            if prev_ep > 0:
                prev_file = scripts_dir / act / f"ep{prev_ep:02d}.md"
                if prev_file.exists():
                    prev_script = prev_file.read_text(encoding="utf-8")[:2000]  # 截断，节省token

        user_msg = (
            f"请写第 {episode_num} 集（{act}）的剧本。\n\n"
            f"## 整体框架\n{framework}\n\n"
        )
        if characters:
            user_msg += f"## 人物设定\n{characters}\n\n"
        if source_text:
            user_msg += f"## 原著参考\n{source_text[:3000]}\n\n"
        if prev_script:
            user_msg += f"## 上一集剧本（概要）\n{prev_script}\n\n"

        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_msg},
        ]

    def parse_output(self, response: str, context: dict) -> dict:
        project = context["project"]
        episode_num = context["episode_num"]
        act = context.get("state", {}).get("act", "")

        # 构建输出路径
        scripts_dir = project.get_path("scripts")
        act_dir = scripts_dir / act
        act_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"ep{episode_num:02d}.md"
        file_path = act_dir / file_name

        # 写入剧本文件
        file_path.write_text(response, encoding="utf-8")

        return {
            "file": str(file_path.relative_to(project.project_root)),
        }

    def offline_output(self, context: dict) -> dict:
        """离线模板剧本：结构合法、含台词与集尾钩子，供下游分镜/配音使用"""
        episode_num = context["episode_num"]
        act = context.get("state", {}).get("act", "")
        project = context["project"]
        work = getattr(project, "source_work", project.name)

        script = f"""# 第{episode_num}集 — {act}

## 场景信息
- 时间：日
- 地点：商府内堂
- 人物：商三官、商士禹

## 剧情梗概
（离线占位剧本）本集承接《{work}》第{episode_num}集剧情，商家遭逢变故，三官立志复仇。

## 分场剧本
### 场景1：商府内堂
烛影摇曳，商三官跪在父亲灵前。

商三官：父亲，女儿一定为您讨回公道。
（攥紧拳头，眼中含泪）

商士禹：（画外）三官，活下去，比什么都重要。

### 场景2：庭院
三官独立庭中，望月不语。

商三官：赵世豪，这笔账，我记下了。
（转身，背影决绝）

## 情感节点
- 开场情绪：悲恸
- 中段转折：立誓复仇
- 收尾情绪：决绝

## 集尾钩子
三官剪下一缕长发，投入火盆——「从今往后，世上再无商三官。」
"""
        return self.parse_output(script, context)
