"""通知模块

完全可选，不配任何渠道也能跑，只写日志。
支持 Telegram、Hermes API、Webhook。
"""

import logging
from pathlib import Path

import httpx

from .config import NotifyConfig

logger = logging.getLogger(__name__)


class BaseNotifier:
    """通知器基类"""

    def send(self, message: str, image_path: str | None = None) -> bool:
        raise NotImplementedError


class TelegramNotifier(BaseNotifier):
    """Telegram Bot 通知"""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_base = f"https://api.telegram.org/bot{bot_token}"

    def send(self, message: str, image_path: str | None = None) -> bool:
        try:
            if image_path:
                with open(image_path, "rb") as f:
                    resp = httpx.post(
                        f"{self.api_base}/sendPhoto",
                        params={"chat_id": self.chat_id, "caption": message},
                        files={"photo": f},
                        timeout=30,
                    )
            else:
                resp = httpx.post(
                    f"{self.api_base}/sendMessage",
                    json={"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"},
                    timeout=15,
                )
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Telegram 通知失败: {e}")
            return False


class HermesNotifier(BaseNotifier):
    """通过 Hermes API Server 发送通知"""

    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip("/")

    def send(self, message: str, image_path: str | None = None) -> bool:
        try:
            payload = {"message": message}
            if image_path:
                payload["image"] = str(image_path)
            resp = httpx.post(f"{self.api_url}/api/notify", json=payload, timeout=15)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Hermes 通知失败: {e}")
            return False


class LogNotifier(BaseNotifier):
    """纯日志通知（兜底，不发送任何外部消息）"""

    def send(self, message: str, image_path: str | None = None) -> bool:
        logger.info(f"[通知] {message}")
        return True


class Notifier:
    """通知管理器 — 多渠道分发"""

    def __init__(self, config: NotifyConfig):
        self.channels: list[BaseNotifier] = []

        if config.telegram_bot_token and config.telegram_chat_id:
            self.channels.append(TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id))

        if config.hermes_api_url:
            self.channels.append(HermesNotifier(config.hermes_api_url))

        # 始终有一个日志兜底
        self.channels.append(LogNotifier())

    def send(self, message: str, image_path: str | None = None) -> None:
        """发送通知到所有已配置的渠道"""
        for ch in self.channels:
            ch.send(message, image_path)
