"""媒体文件有效性验证 + 视频抽帧（M2-4）

真实 provider 的产物不可盲信：下载截断、编码损坏、时长为 0 的文件
都会污染合成。所有生成产物在写状态前先过此处验证；
视频质检前先按多时间点抽帧（DEVELOPMENT_PLAN M2-4）。

探测优先级：ffprobe（JSON 精确解析）→ ffmpeg -i stderr 解析兜底
（部分环境只有单个 ffmpeg 二进制、无 ffprobe）。
"""

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

VIDEO_EXTS = {".mp4", ".webm", ".mov", ".gif"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def resolve_ffprobe(ffprobe: str = "ffprobe", ffmpeg: str | None = None) -> str | None:
    """解析可用的 ffprobe 路径：显式配置（PATH 内或绝对路径）→ ffmpeg 同目录 → PATH。

    找不到返回 None（调用方走 ffmpeg -i 兜底）。
    """
    if ffprobe and (Path(ffprobe).is_file() or shutil.which(ffprobe)):
        return ffprobe
    if ffmpeg:
        sibling = Path(ffmpeg).parent / "ffprobe"
        if sibling.is_file():
            return str(sibling)
    return shutil.which("ffprobe")


def _duration_via_ffmpeg(path: Path, ffmpeg: str) -> float | None:
    """无 ffprobe 时的兜底：解析 `ffmpeg -i` stderr 里的 Duration 行"""
    try:
        r = subprocess.run([ffmpeg, "-i", str(path)], capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"ffmpeg 探测失败 {path}: {e}")
        return None
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    if not m:
        return None
    h, mn, s = m.groups()
    return int(h) * 3600 + int(mn) * 60 + float(s)


def _has_video_stream_via_ffmpeg(path: Path, ffmpeg: str) -> bool:
    try:
        r = subprocess.run([ffmpeg, "-i", str(path)], capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return any("Stream #" in ln and "Video:" in ln for ln in r.stderr.splitlines())


def probe_duration(path: str | Path, ffprobe: str = "ffprobe",
                   ffmpeg: str | None = None) -> float | None:
    """媒体时长（秒）；不可读/无流返回 None"""
    p = Path(path)
    fp = resolve_ffprobe(ffprobe, ffmpeg)
    if fp:
        try:
            out = subprocess.run(
                [fp, "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", str(p)],
                check=True, capture_output=True, text=True, timeout=30,
            )
            data = json.loads(out.stdout)
            return float(data["format"]["duration"])
        except (subprocess.SubprocessError, KeyError, ValueError, OSError) as e:
            logger.debug(f"ffprobe 时长获取失败 {p}（尝试 ffmpeg 兜底）: {e}")
    ffmpeg = ffmpeg or shutil.which("ffmpeg") or "ffmpeg"
    return _duration_via_ffmpeg(p, ffmpeg)


def validate_video(path: str | Path, ffprobe: str = "ffprobe",
                   ffmpeg: str | None = None,
                   min_duration: float = 0.5) -> tuple[bool, str]:
    """视频文件有效性：存在、非空、有视频流、时长达标。返回 (ok, 原因)"""
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return False, "文件不存在或为空"
    if p.suffix.lower() not in VIDEO_EXTS:
        return False, f"非视频扩展名: {p.suffix}"

    fp = resolve_ffprobe(ffprobe, ffmpeg)
    if fp:
        try:
            out = subprocess.run(
                [fp, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_type,duration", "-of", "json",
                 str(p)],
                check=True, capture_output=True, text=True, timeout=30,
            )
            streams = json.loads(out.stdout).get("streams", [])
            if not streams:
                return False, "无视频流"
        except (subprocess.SubprocessError, ValueError, OSError) as e:
            logger.debug(f"ffprobe 流检测失败 {p}（尝试 ffmpeg 兜底）: {e}")
            if not _has_video_stream_via_ffmpeg(p, ffmpeg or "ffmpeg"):
                return False, "无视频流"
    elif not _has_video_stream_via_ffmpeg(p, ffmpeg or "ffmpeg"):
        return False, "无视频流"

    dur = probe_duration(p, ffprobe, ffmpeg)
    if dur is None or dur < min_duration:
        return False, f"时长不足（{dur} < {min_duration}s）"
    return True, ""


def validate_image(path: str | Path) -> tuple[bool, str]:
    """图片文件有效性：存在、非空、PIL 可解码"""
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return False, "文件不存在或为空"
    if p.suffix.lower() not in IMAGE_EXTS:
        return False, f"非图片扩展名: {p.suffix}"
    try:
        from PIL import Image
        with Image.open(p) as img:
            img.verify()
    except Exception as e:
        return False, f"图片无法解码: {e}"
    return True, ""


def frame_timestamps(duration: float, count: int = 3) -> list[float]:
    """多时间点采样：10% / 50% / 90%（避开首尾黑帧与编码边界）"""
    if duration <= 0:
        return [0.0]
    pts = [duration * f for f in (0.1, 0.5, 0.9)]
    return pts[:max(1, count)] if count < 3 else pts


def extract_frames(video: str | Path, out_dir: str | Path,
                   timestamps: list[float], ffmpeg: str = "ffmpeg") -> list[Path]:
    """按时间点抽帧为 png，返回成功生成的帧路径列表"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    video = Path(video)
    frames = []
    for i, t in enumerate(timestamps):
        out = out_dir / f"{video.stem}_frame{i:02d}_{t:.2f}s.png"
        try:
            subprocess.run(
                [ffmpeg, "-y", "-ss", f"{t:.3f}", "-i", str(video),
                 "-frames:v", "1", str(out)],
                check=True, capture_output=True, timeout=60,
            )
            if out.exists() and out.stat().st_size > 0:
                frames.append(out)
        except subprocess.SubprocessError as e:
            logger.warning(f"抽帧失败 {video}@{t:.2f}s: {e}")
    return frames
