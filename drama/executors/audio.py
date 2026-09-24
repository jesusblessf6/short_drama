"""Audio Executor — 配音 + BGM + 字幕

使用 TTS 生成配音，支持 Edge TTS（免费）和即梦配音。

M2-5：
- 角色→音色映射：task.voice_map（角色名→音色，来自 project.yaml 的
  production.voice_map），旁白用 narrator_voice；未映射角色回退默认音色。
- 逐句时间戳：每句 TTS 产物先 ffprobe 实测时长，句首 = 之前时长累加，
  写入 `<ep>.lines.yaml` 清单与 `<ep>.srt` 字幕（对齐策略可解释）。
- 降级静音轨同样产出均匀时间戳的字幕（degraded 显式标记不变）。
"""

import logging
import asyncio
import subprocess
from pathlib import Path

import yaml

from ..utils.media_check import probe_duration
from ..utils.subtitles import build_srt
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
            voice_map: {"商三官": "zh-CN-XiaoxiaoNeural"}  (可选，角色→音色)
            bgm: "optional bgm path or url"
        """
        lines = task["lines"]
        output_path = Path(task["output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        voice_map = task.get("voice_map") or {}

        audio_config = self.config.apis["audio"]
        provider = audio_config.provider

        timings: list[dict] = []
        try:
            if provider == "edge_tts":
                success, timings = self._edge_tts(lines, output_path,
                                                  audio_config, voice_map)
            elif provider == "jimeng":
                success = self._jimeng_tts(lines, output_path, audio_config)
            else:
                return {"success": False, "error": f"不支持的 audio provider: {provider}",
                        "error_class": "param"}
        except Exception as e:
            logger.warning(f"配音生成异常，将降级静音轨: {e}")
            success, timings = False, []

        # 降级：TTS 失败（如无网络）时生成等长静音轨，保证管线不断。
        # degraded 必须显式标记——demo 模式放行（离线跑通用），正式模式调度器拒绝其通过。
        if not success:
            logger.warning("配音失败，降级为静音轨")
            success, timings = self._silent_track(lines, output_path)
            if success:
                return self._finalize(output_path, lines, timings,
                                      source="silent_fallback", degraded=True)

        if success:
            return self._finalize(output_path, lines, timings,
                                  source=provider, degraded=False)
        return {"success": False, "error": "配音与静音降级均失败",
                "error_class": "unknown"}

    # ---------- 结果收尾：写字幕/清单 ----------

    def _finalize(self, output_path: Path, lines: list[dict],
                  timings: list[dict], source: str, degraded: bool) -> dict:
        """把逐句时间戳写入 SRT 与清单文件，结果带回字幕路径"""
        srt_path = output_path.with_suffix(".srt")
        manifest_path = output_path.with_suffix(".lines.yaml")
        try:
            srt_path.write_text(build_srt(timings), encoding="utf-8")
            manifest_path.write_text(yaml.dump(
                {"lines": timings, "source": source, "degraded": degraded},
                allow_unicode=True, default_flow_style=False, sort_keys=False),
                encoding="utf-8")
        except Exception as e:
            logger.warning(f"字幕/清单写入失败（不阻断配音）: {e}")
            srt_path = None
            manifest_path = None
        result = {"success": True, "file": str(output_path), "cost": 0.0,
                  "source": source, "degraded": degraded}
        if srt_path:
            result["subtitles"] = str(srt_path)
            result["lines_manifest"] = str(manifest_path)
        return result

    def _voice_for(self, speaker: str, config, voice_map: dict) -> str:
        """角色→音色：旁白用 narrator_voice，映射表优先，未映射回退默认"""
        if not speaker or speaker == "旁白":
            return config.narrator_voice
        return voice_map.get(speaker, config.voice)

    def _edge_tts(self, lines: list[dict], output_path: Path, config,
                  voice_map: dict) -> tuple[bool, list[dict]]:
        """使用 edge-tts 生成配音；返回 (成功, 逐句时间戳)。

        逐句时间戳：每句 mp3 先 ffprobe 实测时长，句首 = 之前时长累加。
        注意：拼接到最终 output_path 时**不用 -c copy**，让 ffmpeg 按输出扩展名
        重新编码（.wav→pcm），避免「mp3 塞进 wav 容器」的坏文件。
        """
        try:
            import edge_tts
        except ImportError:
            logger.error("edge-tts 未安装，请运行: pip install edge-tts")
            return False, []

        async def generate():
            temp_files = []
            timings = []
            cursor = 0.0
            for i, line in enumerate(lines):
                temp_path = output_path.parent / f"_temp_{i}.mp3"
                speaker = line.get("speaker", "")
                text = (line.get("text") or "").strip() or "。"
                voice = self._voice_for(speaker, config, voice_map)
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(str(temp_path))
                dur = probe_duration(temp_path,
                                     self.config.ffmpeg.ffprobe_path) or 2.0
                timings.append({"speaker": speaker, "text": text,
                                "start": round(cursor, 3),
                                "duration": round(dur, 3)})
                cursor += dur
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
            return True, timings

        try:
            return asyncio.run(generate())
        except Exception as e:
            logger.warning(f"edge-tts 生成失败: {e}")
            return False, []

    def _silent_track(self, lines: list[dict],
                      output_path: Path) -> tuple[bool, list[dict]]:
        """降级：生成等长静音轨（每行约 2s，至少 3s）+ 均匀时间戳"""
        dur = max(3, len(lines) * 2)
        try:
            subprocess.run([
                self.config.ffmpeg.path, "-y",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", str(dur),
                str(output_path),
            ], check=True, capture_output=True)
            logger.info(f"静音轨已生成: {output_path} ({dur}s)")
            per = dur / max(1, len(lines))
            timings = [
                {"speaker": l.get("speaker", "旁白"),
                 "text": (l.get("text") or "").strip(),
                 "start": round(i * per, 3), "duration": round(per, 3)}
                for i, l in enumerate(lines)
            ]
            return True, timings
        except Exception as e:
            logger.error(f"静音轨生成失败: {e}")
            return False, []

    def _jimeng_tts(self, lines: list[dict], output_path: Path, config) -> bool:
        """使用即梦 AI 配音"""
        # TODO: 实现即梦配音 API 调用
        raise NotImplementedError("即梦配音尚未实现")
