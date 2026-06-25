"""Compose Executor — 后期合成

使用 FFmpeg 将视频片段和音轨合成为最终成片。
"""

import logging
import subprocess
import tempfile
from pathlib import Path

from .base import BaseExecutor

logger = logging.getLogger(__name__)


class ComposeExecutor(BaseExecutor):
    """后期合成执行器"""

    def validate_input(self, task: dict) -> bool:
        return "video_clips" in task and "output_path" in task

    def run(self, task: dict) -> dict:
        """
        task 格式:
            video_clips: ["shots/ep01_shot01.mp4", "shots/ep01_shot02.mp4", ...]
            audio_path: "06_音频/ep01.wav"  (可选)
            subtitles: "06_音频/ep01.srt"   (可选)
            output_path: "07_成片/ep01.mp4"
            episode: "ep01"
        """
        video_clips = [Path(c) for c in task["video_clips"]]
        audio_path = Path(task["audio_path"]) if task.get("audio_path") else None
        output_path = Path(task["output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)

        ffmpeg = self.config.ffmpeg.path

        try:
            # 第一步：拼接视频片段
            temp_video = output_path.parent / f"_temp_concat_{task.get('episode', 'out')}.mp4"
            self._concat_videos(video_clips, temp_video, ffmpeg)

            # 第二步：合并音频 + 调色 + 字幕
            if audio_path and audio_path.exists():
                self._merge_audio(temp_video, audio_path, output_path, ffmpeg)
                temp_video.unlink(missing_ok=True)
            else:
                temp_video.rename(output_path)

            # TODO: 字幕、转场、调色、片头片尾

            return {
                "success": True,
                "file": str(output_path),
                "cost": 0.0,
            }

        except Exception as e:
            logger.error(f"合成失败: {e}")
            return {"success": False, "error": str(e)}

    def _concat_videos(self, clips: list[Path], output: Path, ffmpeg: str) -> None:
        """拼接视频片段"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            for clip in clips:
                f.write(f"file '{clip.absolute()}'\n")
            list_path = f.name

        try:
            try:
                # 首选 -c copy（占位片段编码一致时最快）
                subprocess.run([
                    ffmpeg, "-y",
                    "-f", "concat", "-safe", "0",
                    "-i", list_path,
                    "-c", "copy",
                    str(output)
                ], check=True, capture_output=True)
            except subprocess.CalledProcessError:
                # 回退：编码不一致时用 filter_complex concat 重编码
                logger.warning("concat -c copy 失败，回退 filter_complex 重编码")
                self._concat_reencode(clips, output, ffmpeg)
        finally:
            Path(list_path).unlink(missing_ok=True)

    def _concat_reencode(self, clips: list[Path], output: Path, ffmpeg: str) -> None:
        """用 filter_complex concat 重编码拼接（编码不一致时的兜底）"""
        cmd = [ffmpeg, "-y"]
        for clip in clips:
            cmd += ["-i", str(clip.absolute())]
        n = len(clips)
        filt = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[outv]"
        cmd += ["-filter_complex", filt, "-map", "[outv]",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
                str(output)]
        subprocess.run(cmd, check=True, capture_output=True)

    def _merge_audio(self, video: Path, audio: Path, output: Path, ffmpeg: str) -> None:
        """合并视频和音频"""
        subprocess.run([
            ffmpeg, "-y",
            "-i", str(video),
            "-i", str(audio),
            "-c:v", self.config.ffmpeg.default_codec,
            "-c:a", "aac",
            "-crf", str(self.config.ffmpeg.default_crf),
            "-shortest",
            str(output)
        ], check=True, capture_output=True)
