"""Compose Executor — 后期合成

使用 FFmpeg 将视频片段和音轨合成为最终成片。

M2-5 修正与增强：
- 弃用 `-shortest`（按最短流截断，会吞台词或吞画面）：改为探测音频/视频实际
  时长，视频短于音频时用 tpad 克隆末帧补齐，保证台词完整；音频短于视频时
  保留完整视频（结尾无对白是正常状态）。对齐策略写入结果供审计。
- 字幕：SRT 烧录优先（平台发布需要硬字幕），烧录失败（缺 libass/字体）回退
  mov_text 软字幕轨，模式写入结果。
- 产物有效性验证（ffprobe），无效视为合成失败。
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..utils.media_check import probe_duration, validate_video
from .base import BaseExecutor

logger = logging.getLogger(__name__)


class ComposeExecutor(BaseExecutor):
    """后期合成执行器"""

    def validate_input(self, task: dict) -> bool:
        return "video_clips" in task and "output_path" in task

    def run(self, task: dict) -> dict:
        """
        task 格式:
            video_clips: ["shots/ep01_shot01.mp4", ...]
            audio_path: "06_音频/ep01.wav"  (可选)
            subtitles: "06_音频/ep01.srt"   (可选)
            output_path: "07_成片/ep01.mp4"
            episode: "ep01"
        """
        video_clips = [Path(c) for c in task["video_clips"]]
        audio_path = Path(task["audio_path"]) if task.get("audio_path") else None
        subtitles = Path(task["subtitles"]) if task.get("subtitles") else None
        output_path = Path(task["output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        episode = task.get("episode", "out")
        ffmpeg = self.config.ffmpeg.path

        try:
            # 1. 拼接视频片段
            current = output_path.parent / f"_temp_concat_{episode}.mp4"
            self._concat_videos(video_clips, current, ffmpeg)

            # 2. 音频合并 + 对齐补帧（弃 -shortest，台词不截断）
            alignment = {"strategy": "as_is", "video_dur": None, "audio_dur": None}
            if audio_path and audio_path.exists():
                merged = output_path.parent / f"_temp_merged_{episode}.mp4"
                alignment = self._merge_audio(current, audio_path, merged, ffmpeg)
                current.unlink(missing_ok=True)
                current = merged

            # 3. 字幕：烧录优先，失败回退软字幕轨
            subtitle_mode = "none"
            if subtitles and subtitles.exists():
                with_subs = output_path.parent / f"_temp_subs_{episode}.mp4"
                mode = self._add_subtitles(current, subtitles, with_subs, episode)
                if mode != "failed":
                    current.unlink(missing_ok=True)
                    current = with_subs
                    subtitle_mode = mode
                else:
                    with_subs.unlink(missing_ok=True)
                    logger.warning("字幕烧录与软封装均失败，成片无字幕")

            # 4. 产物有效性验证 → 落位
            ok, reason = validate_video(current, self.config.ffmpeg.ffprobe_path,
                                        ffmpeg=self.config.ffmpeg.path)
            if not ok:
                current.unlink(missing_ok=True)
                return {"success": False, "error": f"合成产物无效: {reason}",
                        "error_class": "unknown"}

            if current != output_path:
                current.replace(output_path)

            return {
                "success": True,
                "file": str(output_path),
                "cost": 0.0,
                "source": "ffmpeg",
                "alignment": alignment,
                "subtitle_mode": subtitle_mode,
            }

        except Exception as e:
            logger.error(f"合成失败: {e}")
            return self.fail(e)

    def _concat_videos(self, clips: list[Path], output: Path, ffmpeg: str) -> None:
        """拼接视频片段"""
        if not clips:
            raise ValueError("没有可合成的视频片段")
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

    def _merge_audio(self, video: Path, audio: Path, output: Path,
                     ffmpeg: str) -> dict:
        """合并视频和音频。视频短于音频 → 末帧克隆补齐（台词完整）；不截断任何一侧。"""
        vdur = probe_duration(video, self.config.ffmpeg.ffprobe_path, ffmpeg)
        adur = probe_duration(audio, self.config.ffmpeg.ffprobe_path, ffmpeg)
        alignment = {"strategy": "as_is", "video_dur": vdur, "audio_dur": adur}

        cmd = [ffmpeg, "-y", "-i", str(video), "-i", str(audio)]
        if vdur and adur and vdur < adur - 0.05:
            pad = adur - vdur + 0.2   # 留一点缓冲，避免边界丢帧
            cmd += ["-vf", f"tpad=stop_mode=clone:stop_duration={pad:.3f}"]
            alignment["strategy"] = "video_padded_to_audio"
        cmd += [
            "-c:v", self.config.ffmpeg.default_codec,
            "-c:a", "aac",
            "-crf", str(self.config.ffmpeg.default_crf),
            str(output)
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f"音画对齐: {alignment['strategy']} "
                    f"(video={vdur}s, audio={adur}s)")
        return alignment

    def _add_subtitles(self, video: Path, srt: Path, output: Path,
                       episode: str) -> str:
        """字幕接入。返回 "burned" | "soft" | "failed"。

        烧录：subtitles filter 需 ffmpeg 带 libass（部分精简二进制没有，
        `ffmpeg -filters` 可查）；为绕开路径转义与中文目录问题，把 SRT 复制为
        纯 ASCII 文件名并以输出目录为 cwd 执行。
        软封装：mov_text 字幕轨，`-map 0` 不假定输入必有音频流（无音轨的
        纯视频片段也能封装）。
        """
        ffmpeg = self.config.ffmpeg.path

        # 1) 尝试烧录（硬字幕，平台可见）
        tmp_srt = output.parent / f"_subs_{episode}.srt"
        try:
            shutil.copy(srt, tmp_srt)
            subprocess.run(
                [ffmpeg, "-y", "-i", str(video),
                 "-vf", f"subtitles={tmp_srt.name}",
                 "-c:a", "copy", str(output)],
                check=True, capture_output=True, cwd=str(output.parent),
            )
            if output.exists() and output.stat().st_size > 0:
                return "burned"
        except subprocess.SubprocessError as e:
            stderr = (e.stderr or b"")[-200:].decode(errors="replace")
            logger.warning(f"字幕烧录失败（将回退软字幕）: {stderr or e}")
        finally:
            tmp_srt.unlink(missing_ok=True)

        # 2) 回退：mov_text 软字幕轨（烧录不可用时保底，文件内含字幕轨）
        try:
            subprocess.run(
                [ffmpeg, "-y", "-i", str(video), "-i", str(srt),
                 "-map", "0", "-map", "1:0",
                 "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text",
                 str(output)],
                check=True, capture_output=True,
            )
            if output.exists() and output.stat().st_size > 0:
                return "soft"
        except subprocess.SubprocessError as e:
            stderr = (e.stderr or b"")[-200:].decode(errors="replace")
            logger.warning(f"软字幕封装失败: {stderr or e}")

        output.unlink(missing_ok=True)
        return "failed"
