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
                ok, reason = self._verify(output_path)
                if not ok:
                    return {"success": False, "error": f"占位视频无效: {reason}",
                            "error_class": "unknown", "attempts": 1}
                return {"success": True, "file": str(output_path),
                        "cost": 0.0, "attempts": 1, "source": "placeholder"}
            except Exception as e:
                logger.error(f"占位视频生成失败: {e}")
                return self.fail(e, attempts=1)

        # 方舟 Agent Plan：异步任务（提交/轮询/下载），支持凭 external_task_id 恢复
        if provider == "ark":
            try:
                return self._run_ark(task, api_config, image_path,
                                     output_path, duration)
            except Exception as e:
                logger.error(f"方舟视频生成失败: {e}")
                return self.fail(e, attempts=1)

        try:
            if provider == "kling":
                video_data = self._call_kling(image_path, prompt, api_config)
            elif provider == "seedance":
                video_data = self._call_seedance(image_path, prompt, api_config)
            elif provider == "runway":
                video_data = self._call_runway(image_path, prompt, api_config)
            else:
                return {"success": False, "error": f"不支持的 provider: {provider}",
                        "error_class": "param"}

            if video_data:
                output_path.write_bytes(video_data)
                ok, reason = self._verify(output_path)
                if not ok:
                    # 下载/写入产物损坏：按可重试失败处理（重新生成而非直接 approved）
                    return {"success": False, "error": f"生成文件无效: {reason}",
                            "error_class": "unknown", "attempts": 1}
                return {
                    "success": True,
                    "file": str(output_path),
                    "cost": api_config.cost_per_call,
                    "attempts": 1,
                    "source": provider,
                }
            else:
                return {"success": False, "error": "生成返回空数据",
                        "error_class": "unknown"}

        except Exception as e:
            logger.error(f"图生视频失败: {e}")
            return self.fail(e, attempts=1)

    def _verify(self, output_path: Path) -> tuple[bool, str]:
        """产物有效性验证（M2-4）：有视频流且时长达标（ffprobe 缺失时 ffmpeg 兜底）"""
        from ..utils.media_check import validate_video
        return validate_video(output_path, self.config.ffmpeg.ffprobe_path,
                              ffmpeg=self.config.ffmpeg.path)

    # ---------- 方舟 Agent Plan 视频生成（异步任务） ----------

    POLL_INTERVAL = 5.0    # 轮询间隔（秒）
    POLL_TIMEOUT = 300.0   # 单次 run() 内的最长等待；超时交还任务 ID 下轮续轮询

    def _run_ark(self, task: dict, config, image_path: Path,
                 output_path: Path, duration: int) -> dict:
        """提交→轮询→下载。已契合 M1 恢复契约：

        - task 带 external_task_id（中断恢复）→ 跳过提交直接轮询，不重复扣费
        - 轮询超时 → 返回 {success: False, submitted: True, external_task_id}，
          状态保持 generating，下轮凭 ID 续轮询（不算失败、不耗 attempts）
        - 任务失败（failed/cancelled）→ 可重试失败（重新提交新任务）
        """
        import base64
        import time as _time
        import httpx

        headers = {"Authorization": f"Bearer {config.api_key}"}
        base = config.base_url.rstrip("/")
        task_id = task.get("external_task_id")

        if not task_id:
            mime = "image/png" if image_path.suffix == ".png" else "image/jpeg"
            first_frame = (f"data:{mime};base64,"
                           + base64.b64encode(image_path.read_bytes()).decode())
            lo, hi = self._duration_range(config.model)
            body = {
                "model": config.model,
                "content": [
                    {"type": "text", "text": task["prompt"]},
                    {"type": "image_url", "image_url": {"url": first_frame}},
                ],
                "ratio": config.extra.get("ratio", "9:16"),
                "duration": max(lo, min(hi, int(duration))),
                "generate_audio": config.extra.get("generate_audio", False),
                "watermark": False,
            }
            resp = httpx.post(f"{base}/contents/generations/tasks",
                              json=body, headers=headers, timeout=60)
            resp.raise_for_status()
            task_id = resp.json().get("id")
            if not task_id:
                raise RuntimeError(f"ark 任务提交未返回 id: {resp.json()}")
            logger.info(f"ark 视频任务已提交: {task_id}")

        deadline = _time.monotonic() + self.POLL_TIMEOUT
        while _time.monotonic() < deadline:
            resp = httpx.get(f"{base}/contents/generations/tasks/{task_id}",
                             headers=headers, timeout=30)
            resp.raise_for_status()
            info = resp.json()
            status = info.get("status")
            if status == "succeeded":
                video_url = (info.get("content") or {}).get("video_url")
                if not video_url:
                    raise RuntimeError(f"ark 任务成功但无 video_url: {info}")
                video = httpx.get(video_url, timeout=300, follow_redirects=True)
                video.raise_for_status()
                output_path.write_bytes(video.content)
                ok, reason = self._verify(output_path)
                if not ok:
                    return {"success": False, "error": f"生成文件无效: {reason}",
                            "error_class": "unknown", "attempts": 1}
                return {"success": True, "file": str(output_path),
                        "cost": config.cost_per_call, "attempts": 1,
                        "source": "ark"}
            if status in ("failed", "cancelled"):
                error = info.get("error") or info.get("last_error") or status
                return {"success": False, "error": f"ark 任务 {status}: {error}",
                        "error_class": "unknown", "attempts": 1}
            logger.info(f"ark 视频任务 {task_id}: {status}，"
                        f"{self.POLL_INTERVAL:.0f}s 后再查")
            _time.sleep(self.POLL_INTERVAL)

        # 单轮等待耗尽：交还任务 ID，保持 generating 状态续轮询（M1 契约）
        return {"success": False, "submitted": True, "external_task_id": task_id}

    @staticmethod
    def _duration_range(model: str) -> tuple[int, int]:
        """模型支持的时长范围：seedance-2.5 为 4-30s，其余 4-15s（官方能力表）"""
        return (4, 30) if "2.5" in model or "2-5" in model else (4, 15)

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
