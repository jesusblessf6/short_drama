"""Text2Img Executor — 文生图

调用即梦/Midjourney/Flux API，根据提示词生成图片。
"""

import io
import logging
import textwrap
from pathlib import Path

from .base import BaseExecutor

logger = logging.getLogger(__name__)

# 占位图中文字体候选（macOS 实测可用；逐个尝试，全失败退 PIL 默认字体）
FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]


class Text2ImgExecutor(BaseExecutor):
    """文生图执行器"""

    def validate_input(self, task: dict) -> bool:
        return "prompt" in task and "output_path" in task

    def run(self, task: dict) -> dict:
        """
        task 格式:
            prompt: "中景，商三官（...），站立抚琴，..."
            negative_prompt: "毁容，面部扭曲，..."
            reference_images: ["path/to/ref.png"]
            output_path: "05_美术/shots/ep01_shot01.png"
            shot_id: "ep01_shot01"
        """
        prompt = task["prompt"]
        negative_prompt = task.get("negative_prompt", "")
        output_path = Path(task["output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)

        api_config = self.config.apis["text2img"]
        provider = api_config.provider

        # 本地占位 provider：无需任何外部 API，直接渲染占位图
        if provider == "placeholder":
            try:
                self._call_placeholder(prompt, task.get("shot_id", ""),
                                       task.get("scene", ""), output_path)
                ok, reason = self._verify(output_path)
                if not ok:
                    return {"success": False, "error": f"占位图无效: {reason}",
                            "error_class": "unknown", "attempts": 1}
                return {"success": True, "file": str(output_path),
                        "cost": 0.0, "attempts": 1, "source": "placeholder"}
            except Exception as e:
                logger.error(f"占位图生成失败: {e}")
                return self.fail(e, attempts=1)

        try:
            if provider == "ark":
                image_data = self._call_ark(prompt, negative_prompt, api_config,
                                            task.get("reference_images") or [])
            elif provider == "jimeng":
                image_data = self._call_jimeng(prompt, negative_prompt, api_config)
            elif provider == "midjourney":
                image_data = self._call_midjourney(prompt, api_config)
            elif provider == "flux":
                image_data = self._call_flux(prompt, api_config)
            else:
                return {"success": False, "error": f"不支持的 provider: {provider}",
                        "error_class": "param"}

            if image_data:
                output_path.write_bytes(image_data)
                ok, reason = self._verify(output_path)
                if not ok:
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
            logger.error(f"文生图失败: {e}")
            return self.fail(e, attempts=1)

    def _verify(self, output_path: Path) -> tuple[bool, str]:
        """产物有效性验证（M2-4）：PIL 可解码"""
        from ..utils.media_check import validate_image
        return validate_image(output_path)

    def _call_ark(self, prompt: str, negative: str, config,
                  reference_images: list) -> bytes:
        """火山方舟 Agent Plan 企业版图片生成（同步）。

        端点/参数依据《接入视觉模型》官方文档（references/ 下 PDF）：
        POST {base_url}/images/generations
        body: model/prompt/size/output_format/response_format/watermark
        返回 data[0].url（或 b64_json）。参考图（角色一致性）经 image 字段传
        base64 data URL；负面提示词并入 prompt（seedream 无独立 negative 字段）。
        """
        import base64
        import httpx

        body = {
            "model": config.model,
            "prompt": f"{prompt}\n（避免出现：{negative}）" if negative else prompt,
            "size": config.extra.get("size", "1080x1920"),   # 竖屏 9:16
            "output_format": "png",
            "response_format": "url",
            "watermark": False,
        }
        refs = []
        for ref in reference_images[:5]:
            p = Path(ref)
            if p.exists():
                mime = "image/png" if p.suffix == ".png" else "image/jpeg"
                refs.append(f"data:{mime};base64,"
                            + base64.b64encode(p.read_bytes()).decode())
        if refs:
            body["image"] = refs[0] if len(refs) == 1 else refs

        resp = httpx.post(
            f"{config.base_url.rstrip('/')}/images/generations",
            json=body,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            raise RuntimeError("ark 图片生成返回空 data")
        item = data[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        url = item.get("url")
        if not url:
            raise RuntimeError(f"ark 响应无 url/b64_json: {item}")
        img = httpx.get(url, timeout=120, follow_redirects=True)
        img.raise_for_status()
        return img.content

    def _call_jimeng(self, prompt: str, negative: str, config) -> bytes:
        """调用即梦 API"""
        # TODO: 实现即梦 API 调用
        # API 文档: https://team.jimeng.jiyun.com/docs/
        # 这里先写骨架
        logger.info(f"调用即梦 API (model={config.model})")

        # 示例骨架：
        # resp = httpx.post(
        #     f"https://jimeng.jiyun.com/api/v1/text2img",
        #     headers={"Authorization": f"Bearer {config.api_key}"},
        #     json={
        #         "model": config.model,
        #         "prompt": prompt,
        #         "negative_prompt": negative,
        #     },
        #     timeout=60,
        # )
        # result = resp.json()
        # return base64.b64decode(result["images"][0])

        raise NotImplementedError("即梦 API 调用尚未实现")

    def _call_midjourney(self, prompt: str, config) -> bytes:
        """调用 Midjourney API"""
        # TODO: 通过 Midjourney API 代理调用
        raise NotImplementedError("Midjourney API 调用尚未实现")

    def _call_flux(self, prompt: str, config) -> bytes:
        """调用 Flux API"""
        # TODO: 通过 Fal 或 Replicate 调用 Flux
        raise NotImplementedError("Flux API 调用尚未实现")

    # ---- 本地占位 provider ----

    @staticmethod
    def _load_font(size: int):
        """逐个尝试中文字体候选，全失败退默认字体"""
        from PIL import ImageFont
        for path in FONT_CANDIDATES:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def _call_placeholder(self, prompt: str, shot_id: str, scene: str,
                          output_path: Path) -> None:
        """用 Pillow 渲染一张 9:16 占位图：shot_id + 场景 + prompt 文字"""
        from PIL import Image, ImageDraw

        W, H = 1080, 1920
        img = Image.new("RGB", (W, H), (26, 28, 38))
        draw = ImageDraw.Draw(img)

        f_id = self._load_font(72)
        f_scene = self._load_font(48)
        f_body = self._load_font(38)

        margin = 80
        y = 140
        draw.text((margin, y), f"[占位] {shot_id}", font=f_id, fill=(240, 220, 120))
        y += 140
        if scene:
            for line in textwrap.wrap(f"场景：{scene}", width=18):
                draw.text((margin, y), line, font=f_scene, fill=(180, 210, 255))
                y += 64
        y += 30
        # prompt 按中文宽度手动折行
        for line in textwrap.wrap(prompt, width=20):
            draw.text((margin, y), line, font=f_body, fill=(225, 225, 225))
            y += 56
            if y > H - margin:
                break

        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path, "PNG")
        logger.info(f"占位图已生成: {output_path}")
