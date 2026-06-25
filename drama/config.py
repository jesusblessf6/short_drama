"""配置加载模块

从 config.yaml 读取全局配置，从 project.yaml 读取项目配置。
环境变量 ${VAR} 语法自动替换。
"""

import os
import re
from pathlib import Path
from dataclasses import dataclass, field, fields
from typing import Any

import yaml


ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _resolve_env_vars(value: Any) -> Any:
    """递归替换字符串中的 ${VAR} 为环境变量值"""
    if isinstance(value, str):
        def replacer(m):
            return os.environ.get(m.group(1), "")
        return ENV_VAR_PATTERN.sub(replacer, value)
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


@dataclass
class LLMConfig:
    provider: str
    base_url: str
    api_key: str
    model: str
    vision_model: str
    max_tokens: int
    temperature: float
    offline: bool = False
    price_per_1k_tokens: float = 0.0   # ¥/千token，用于 token→钱折算

    @property
    def is_offline(self) -> bool:
        """显式 offline 或 api_key 为空（未配 key）→ 走离线模板模式"""
        return bool(self.offline) or not self.api_key


@dataclass
class APIConfig:
    provider: str
    api_key: str
    model: str
    cost_per_call: float = 0.0


@dataclass
class AudioConfig:
    provider: str
    voice: str


@dataclass
class OrchestratorConfig:
    parallel_episodes: int
    parallel_shots: int
    tick_interval: int
    state_dir: str


@dataclass
class NotifyConfig:
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    hermes_api_url: str = ""


@dataclass
class FFmpegConfig:
    path: str = "ffmpeg"
    default_codec: str = "libx264"
    default_crf: int = 23


@dataclass
class Config:
    llm: LLMConfig
    apis: dict  # {text2img: APIConfig, img2video: APIConfig, audio: AudioConfig}
    orchestrator: OrchestratorConfig
    notify: NotifyConfig
    ffmpeg: FFmpegConfig
    corpus_dir: str
    corpus_sources: list
    raw: dict  # 原始 YAML dict，供扩展用

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        path = Path(path)
        with open(path) as f:
            raw = yaml.safe_load(f)
        raw = _resolve_env_vars(raw)

        llm = LLMConfig(**raw["llm"])
        apis = {
            "text2img": APIConfig(**raw["apis"]["text2img"]),
            "img2video": APIConfig(**raw["apis"]["img2video"]),
            "audio": AudioConfig(**raw["apis"]["audio"]),
        }
        orch = OrchestratorConfig(**raw["orchestrator"])
        notify_raw = raw.get("notify", {})
        notify = NotifyConfig(
            telegram_bot_token=notify_raw.get("telegram", {}).get("bot_token", ""),
            telegram_chat_id=notify_raw.get("telegram", {}).get("chat_id", ""),
            hermes_api_url=notify_raw.get("hermes_api_url", ""),
        )
        ffmpeg = FFmpegConfig(**raw.get("ffmpeg", {}))

        return cls(
            llm=llm,
            apis=apis,
            orchestrator=orch,
            notify=notify,
            ffmpeg=ffmpeg,
            corpus_dir=raw.get("corpus", {}).get("dir", "corpus"),
            corpus_sources=raw.get("corpus", {}).get("sources", []),
            raw=raw,
        )


@dataclass
class ProjectConfig:
    """项目配置，从 project.yaml 加载"""
    name: str
    source_work: str
    source_author: str
    source_dynasty: str
    episodes: int
    episode_duration: str
    aspect_ratio: str
    paths: dict
    acts: list
    production: dict
    project_root: Path  # 项目目录的绝对路径

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ProjectConfig":
        path = Path(path)
        with open(path) as f:
            raw = yaml.safe_load(f)
        # 只取 dataclass 已知字段，避免 project.yaml 多一个字段就 TypeError
        known = {f.name for f in fields(cls)} - {"project_root"}
        filtered = {k: v for k, v in raw.items() if k in known}
        ignored = set(raw) - known
        if ignored:
            import logging
            logging.getLogger(__name__).debug(f"project.yaml 忽略未知字段: {ignored}")
        return cls(**filtered, project_root=path.parent)

    def get_path(self, key: str) -> Path:
        """获取 paths 中配置的路径的绝对路径"""
        rel = self.paths[key]
        return self.project_root / rel

    def get_episode_range(self, act_name: str) -> tuple[int, int]:
        """获取某幕的集数范围"""
        for act in self.acts:
            if act["name"] == act_name:
                return tuple(act["episodes"])
        raise ValueError(f"幕 '{act_name}' 不存在")

    def get_act_for_episode(self, ep_num: int) -> str:
        """根据集号返回所属幕名"""
        for act in self.acts:
            start, end = act["episodes"]
            if start <= ep_num <= end:
                return act["name"]
        raise ValueError(f"第 {ep_num} 集不在任何幕中")
