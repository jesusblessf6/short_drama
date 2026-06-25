"""LLM 调用封装

统一封装 OpenAI 兼容格式的 LLM 调用。
支持文本对话和 vision（图片输入）。
"""

import logging
from typing import Any

from openai import OpenAI

from .config import LLMConfig

logger = logging.getLogger(__name__)


class LLMClient:
    """LLM 调用客户端"""

    def __init__(self, config: LLMConfig):
        self.config = config
        # 离线模式（无 api_key）下用占位 key 构造客户端，避免 OpenAI() 因缺凭据报错；
        # 离线时调用方会短路、不会真正请求。
        self.client = OpenAI(
            base_url=config.base_url,
            api_key=config.api_key or "offline-placeholder",
        )
        self.last_usage = 0

    def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """文本对话

        Args:
            messages: OpenAI 格式的 messages 列表
            model: 模型名，默认用 config 中的 model
            max_tokens: 最大输出 token，默认用 config
            temperature: 温度，默认用 config

        Returns:
            LLM 回复的文本内容
        """
        response = self.client.chat.completions.create(
            model=model or self.config.model,
            messages=messages,
            max_tokens=max_tokens or self.config.max_tokens,
            temperature=temperature if temperature is not None else self.config.temperature,
        )
        self.last_usage = response.usage.total_tokens
        return response.choices[0].message.content

    def chat_with_image(
        self,
        messages: list[dict],
        image_path: str,
        model: str | None = None,
    ) -> str:
        """带图片输入的对话（vision）

        Args:
            messages: OpenAI 格式的 messages 列表
            image_path: 图片文件路径
            model: vision 模型名，默认用 config 中的 vision_model

        Returns:
            LLM 回复的文本内容
        """
        import base64
        from pathlib import Path

        img_path = Path(image_path)
        with open(img_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode()
        ext = img_path.suffix.lstrip(".")
        mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"

        # 在最后一条 user message 中插入图片
        messages = messages.copy()
        last_msg = messages[-1]
        if last_msg["role"] == "user":
            messages[-1] = {
                "role": "user",
                "content": [
                    {"type": "text", "text": last_msg["content"]},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_data}"}},
                ],
            }

        response = self.client.chat.completions.create(
            model=model or self.config.vision_model,
            messages=messages,
            max_tokens=self.config.max_tokens,
        )
        self.last_usage = response.usage.total_tokens
        return response.choices[0].message.content

    def chat_with_image_url(
        self,
        messages: list[dict],
        image_url: str,
        model: str | None = None,
    ) -> str:
        """带图片 URL 的对话（vision）"""
        messages = messages.copy()
        last_msg = messages[-1]
        if last_msg["role"] == "user":
            messages[-1] = {
                "role": "user",
                "content": [
                    {"type": "text", "text": last_msg["content"]},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }

        response = self.client.chat.completions.create(
            model=model or self.config.vision_model,
            messages=messages,
            max_tokens=self.config.max_tokens,
        )
        self.last_usage = response.usage.total_tokens
        return response.choices[0].message.content
