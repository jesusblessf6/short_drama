"""Audio Executor — 配音 + BGM

使用 TTS 生成配音音频，支持 Edge TTS（免费）和即梦配音。
"""

import logging
import asyncio
import subprocess
from pathlib import Path

from .base import BaseExecutor

logger = logging.getLogger(__name__)


class AudioExecutor(BaseExecutor):
    """音频生成执行器"""

    def validate_input(self, task: dict) -> bool:
        return "lines" in task and "output_path" in task

    def run(self, task: dict) -> dict:
        """
        task 格式:
            lines: [
                {"speaker": "商三官", "text": "父亲，女儿一定为您讨回公道"},
                {"speaker": "旁白", "text": "那一夜，商三官剪断了长发"},
            ]
            output_path: "06_音频/ep01.wav"
            bgm: "optional bgm path or url"
        """
        lines = task["lines"]
        output_path = Path(task["output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)

        audio_config = self.config.apis["audio"]
        provider = audio_config.provider

        try:
            if provider == "edge_tts":
                success = self._edge_tts(lines, output_path, audio_config)
            elif provider == "jimeng":
                success = self._jimeng_tts(lines, output_path, audio_config)
            else:
                return {"success": False, "error": f"不支持的 audio provider: {provider}"}
        except Exception as e:
            logger.warning(f"配音生成异常，将降级静音轨: {e}")
            success = False

        # 降级：TTS 失败（如无网络）时生成等长静音轨，保证管线不断
        if not success:
            logger.warning("配音失败，降级为静音轨")
            success = self._silent_track(lines, output_path)

        if success:
            return {"success": True, "file": str(output_path), "cost": 0.0}
        return {"success": False, "error": "配音与静音降级均失败"}

    def _edge_tts(self, lines: list[dict], output_path: Path, config) -> bool:
        """使用 edge-tts 生成配音；任何失败返回 False（由上层降级静音轨）。

        注意：拼接到最终 output_path 时**不用 -c copy**，让 ffmpeg 按输出扩展名
        重新编码（.wav→pcm），避免「mp3 塞进 wav 容器」的坏文件。
        """
        try:
            import edge_tts
        except ImportError:
            logger.error("edge-tts 未安装，请运行: pip install edge-tts")
            return False

        async def generate():
            temp_files = []
            for i, line in enumerate(lines):
                temp_path = output_path.parent / f"_temp_{i}.mp3"
                speaker = line.get("speaker", "")
                text = (line.get("text") or "").strip() or "。"
                voice = config.voice if speaker != "旁白" else "zh-CN-YunxiNeural"
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(str(temp_path))
                temp_files.append(temp_path)

            list_file = output_path.parent / "_concat_list.txt"
            list_file.write_text(
                "".join(f"file '{tf.absolute()}'\n" for tf in temp_files),
                encoding="utf-8",
            )
            subprocess.run([
                self.config.ffmpeg.path, "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                str(output_path),
            ], check=True, capture_output=True)

            list_file.unlink(missing_ok=True)
            for tf in temp_files:
                tf.unlink(missing_ok=True)
            return True

        try:
            return asyncio.run(generate())
        except Exception as e:
            logger.warning(f"edge-tts 生成失败: {e}")
            return False

    def _silent_track(self, lines: list[dict], output_path: Path) -> bool:
        """降级：生成等长静音轨（每行约 2s，至少 3s）"""
        dur = max(3, len(lines) * 2)
        try:
            subprocess.run([
                self.config.ffmpeg.path, "-y",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", str(dur),
                str(output_path),
            ], check=True, capture_output=True)
            logger.info(f"静音轨已生成: {output_path} ({dur}s)")
            return True
        except Exception as e:
            logger.error(f"静音轨生成失败: {e}")
            return False

    def _jimeng_tts(self, lines: list[dict], output_path: Path, config) -> bool:
        """使用即梦 AI 配音"""
        # TODO: 实现即梦配音 API 调用
        raise NotImplementedError("即梦配音尚未实现")
