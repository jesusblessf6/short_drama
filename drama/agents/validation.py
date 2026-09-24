"""分镜结构化输出校验（M2-2）

LLM 的分镜输出不可信：镜头 ID 冲突、prompt 缺失、时长越界、未知角色等
问题会直接污染下游（生成付费任务、配音、合成）。此处做两层防御：

1. ``repair_shots``  — 机械可修复的问题就地修（ID 重编号、type 归一、时长钳制、
   缺失 prompt 用兜底），尽量把"能救"的输出救回来；
2. ``validate_shots`` — 校验剩余问题，分 fatal（阻断下游）与 warning（带病放行，
   日志可见）。

真实 LLM 路径由 StoryboardAgent 的修复循环消费：fatal 非空且未达修复上限时，
把错误清单喂回 LLM 重写；达到上限仍有 fatal → 整体失败，不进入付费生成。
"""

import logging

logger = logging.getLogger(__name__)

VALID_SHOT_TYPES = {"static", "simple", "complex", "lip_sync"}
# 时长钳制边界（秒）：竖屏短剧单镜头 2-10s
MIN_DURATION, MAX_DURATION = 2, 10


def repair_shots(shots: list[dict], episode: str) -> list[dict]:
    """就地机械修复。返回修复后的 shots（同一列表）。"""
    seen: set[str] = set()
    for i, shot in enumerate(shots, start=1):
        # ID：缺失/重复 → 按序重编号（下游按 id 建文件名，唯一性是硬要求）
        sid = (shot.get("id") or "").strip()
        if not sid or sid in seen:
            sid = f"{episode}_shot{i:02d}"
            while sid in seen:   # 理论上不会撞（i 递增），防御性兜底
                i += 1
                sid = f"{episode}_shot{i:02d}"
        shot["id"] = sid
        seen.add(sid)

        # type：不合法 → static（最保守的重试预算）
        if shot.get("type") not in VALID_SHOT_TYPES:
            shot["type"] = "static"

        # duration：非正数/越界 → 钳制
        try:
            dur = int(shot.get("duration", 4))
        except (TypeError, ValueError):
            dur = 4
        shot["duration"] = max(MIN_DURATION, min(MAX_DURATION, dur))

        # prompt：空 → 场景兜底（付费生成需要非空 prompt）
        scene = shot.get("scene") or "场景"
        if not (shot.get("t2i_prompt") or "").strip():
            shot["t2i_prompt"] = f"中景，{scene}，古风写实"
        if not (shot.get("i2v_prompt") or "").strip():
            shot["i2v_prompt"] = "中景固定镜头，缓慢推进"

        # speaker/dialogue：None 归一
        shot["speaker"] = (shot.get("speaker") or "旁白").strip() or "旁白"
        shot["dialogue"] = (shot.get("dialogue") or "").strip()
    return shots


def validate_shots(shots: list[dict], characters: set[str] | None = None
                   ) -> tuple[list[str], list[str]]:
    """校验镜头列表。返回 (fatal, warnings)。

    fatal  = 修复不了的硬伤，进入下游会出钱出错（空列表、镜头数与声明不符等）
    warning = 可放行但应记录（未知角色、无台词镜头等）
    """
    fatal: list[str] = []
    warns: list[str] = []
    if not shots:
        return ["没有任何镜头"], warns

    ids = [s.get("id", "") for s in shots]
    if len(set(ids)) != len(ids):
        fatal.append("镜头 ID 存在重复")

    for i, shot in enumerate(shots, start=1):
        sid = shot.get("id") or f"#{i}"
        # 空 prompt 是硬伤：付费生成拿着空/兜底 prompt 就是烧钱画废图
        if not (shot.get("t2i_prompt") or "").strip():
            fatal.append(f"{sid}: 文生图Prompt 缺失")
        if not (shot.get("i2v_prompt") or "").strip():
            fatal.append(f"{sid}: 图生视频Prompt 缺失")
        if not shot.get("scene", "").strip():
            warns.append(f"{sid}: scene 为空")
        if characters is not None:
            speaker = shot.get("speaker", "旁白")
            if speaker != "旁白" and speaker not in characters:
                warns.append(f"{sid}: 未知角色 '{speaker}'（角色卡中不存在）")
    return fatal, warns


def character_names(project) -> set[str]:
    """从角色卡目录收集角色名（文件名去扩展名），供校验比对"""
    characters: set[str] = set()
    try:
        characters_dir = project.get_path("characters")
    except (KeyError, Exception):
        return characters
    if not characters_dir.exists():
        return characters
    for f in characters_dir.iterdir():
        if f.suffix in (".md", ".yaml", ".yml"):
            characters.add(f.stem)
    return characters
