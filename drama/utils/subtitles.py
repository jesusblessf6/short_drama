"""SRT 字幕生成（M2-5）

字幕时间线由逐句音频实测时长累积而来（每句 TTS 产物先 ffprobe 再拼接），
对齐策略可解释：句首时间 = 之前所有句音频时长之和。
"""

from datetime import timedelta


def format_srt_time(seconds: float) -> str:
    """秒 → SRT 时间戳 `HH:MM:SS,mmm`"""
    ms = max(0, int(round(seconds * 1000)))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(lines: list[dict]) -> str:
    """由逐句时间信息构建 SRT 文本。

    lines: [{"start": 秒, "duration": 秒, "text": 台词}]（按 start 升序）
    无 text/空文本的行跳过；时间缺失的行按上一句顺延 2s 兜底。
    """
    blocks = []
    idx = 0
    cursor = 0.0
    for line in sorted(lines, key=lambda x: x.get("start", 0.0)):
        text = (line.get("text") or "").strip()
        if not text:
            continue
        start = line.get("start")
        dur = line.get("duration")
        if start is None:
            start = cursor
        if not dur or dur <= 0:
            dur = 2.0
        end = start + dur
        idx += 1
        blocks.append(
            f"{idx}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{text}\n")
        cursor = end
    return "\n".join(blocks)
