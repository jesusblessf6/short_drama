"""Img2Video Executor — 图生视频

调用可灵/Seedance/Runway API，根据分镜图和提示词生成视频片段。
"""

import logging
import subprocess
import time
from pathlib import Path

from .base import BaseExecutor

logger = logging.getLogger(__name__)


class Img2VideoExecutor(BaseExecutor):
    """图生视频执行器"""

    def validate_input(self, task: dict) -> bool:
        return "image_path" in task and "prompt" in task and "output_path" in task

    def run(self, task: dict) -> dict:
        """
        task 格式:
            image_path: "05_美术/shots/ep01_shot01.png"
            prompt: "中景固定镜头，缓慢推进，商三官手指拨动琴弦..."
            output_path: "05_美术/shots/ep01_shot01.mp4"
            shot_id: "ep01_shot01"
        """
        image_path = Path(task["image_path"])
        prompt = task["prompt"]
        output_path = Path(task["output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        duration = int(task.get("duration", 4))

        api_config = self.config.apis["img2video"]
        provider = api_config.provider

        # 本地占位 provider：ffmpeg 把静帧做成 N 秒 mp4
        if provider == "placeholder":
            try:
                self._call_placeholder(image_path, output_path, duration)
                return {"success": True, "file": str(output_path),
                        "cost": 0.0, "attempts": 1}
            except Exception as e:
                logger.error(f"占位视频生成失败: {e}")
                return {"success": False, "error": str(e), "attempts": 1}

        try:
            if provider == "kling":
                video_data = self._call_kling(image_path, prompt, api_config)
            elif provider == "seedance":
                video_data = self._call_seedance(image_path, prompt, api_config)
            elif provider == "runway":
                video_data = self._call_runway(image_path, prompt, api_config)
            else:
                return {"success": False, "error": f"不支持的 provider: {provider}"}

            if video_data:
                output_path.write_bytes(video_data)
                return {
                    "success": True,
                    "file": str(output_path),
                    "cost": api_config.cost_per_call,
                    "attempts": 1,
                }
            else:
                return {"success": False, "error": "生成返回空数据"}

        except Exception as e:
            logger.error(f"图生视频失败: {e}")
            return {"success": False, "error": str(e), "attempts": 1}

    def _call_kling(self, image_path: Path, prompt: str, config) -> bytes:
        """调用可灵 API"""
        # TODO: 实现可灵 API 调用
        # API 文档: https://kling.kuaishou.com/docs
        # 可灵是异步API：提交任务 → 轮询状态 → 下载结果
        logger.info(f"调用可灵 API (model={config.model})")

        # 示例骨架：
        # 1. 上传图片，提交生成任务
        # task_id = self._submit_kling_task(image_path, prompt, config)
        #
        # 2. 轮询任务状态
        # while True:
        #     status = self._check_kling_task(task_id, config)
        #     if status["done"]:
        #         break
        #     time.sleep(5)
        #
        # 3. 下载视频
        # return httpx.get(status["video_url"]).content

        raise NotImplementedError("可灵 API 调用尚未实现")

    def _call_seedance(self, image_path: Path, prompt: str, config) -> bytes:
        """调用 Seedance API"""
        # TODO: 实现即梦 Seedance API 调用
        raise NotImplementedError("Seedance API 调用尚未实现")

    def _call_runway(self, image_path: Path, prompt: str, config) -> bytes:
        """调用 Runway Gen-3 API"""
        # TODO: 实现 Runway API 调用
        raise NotImplementedError("Runway API 调用尚未实现")

    # ---- 本地占位 provider ----

    def _call_placeholder(self, image_path: Path, output_path: Path,
                          duration: int) -> None:
        """ffmpeg 把静帧做成 N 秒 mp4。

        统一编码参数（1080x1920 / yuv420p / 25fps / libx264），保证后续
        compose 的 concat -c copy 可用（所有片段编码一致）。
        """
        ffmpeg = self.config.ffmpeg.path
        cmd = [
            ffmpeg, "-y",
            "-loop", "1", "-i", str(image_path),
            "-t", str(duration),
            "-r", "25",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,"
                   "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
            str(output_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f"占位视频已生成: {output_path} ({duration}s)")
