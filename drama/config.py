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
    narrator_voice: str = "zh-CN-YunxiNeural"


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
    ffprobe_path: str = "ffprobe"
    default_codec: str = "libx264"
    default_crf: int = 23


@dataclass
class BudgetConfig:
    """三级预算（¥）。None = 该级不限。提交付费任务前按单次预估检查，超出即阻止。"""
    per_shot_cny: float | None = None
    per_episode_cny: float | None = None
    project_cny: float | None = None


@dataclass
class JudgmentConfig:
    """判断层（Jev 式类型化决策）配置。provider=rule 时判断层弃权、行为与历史一致。

    mock=true 不发网络请求（确定性启发式，验证接线）；接真实 Jev 云改 mock=false+api_key；
    换本地开源判断模型（jevos 等，同一 wire 协议）改 endpoint 为本地地址即可。
    """
    provider: str = "rule"                  # rule | jev
    endpoint: str = "https://api.typesafe.ai"
    api_key: str = ""
    model: str = "jev-latest"
    mock: bool = True
    timeout: float = 5.0
    min_confidence: float = 0.9             # 低于此置信度 → 视同弃权走既有路径
    redline_gate: str = "log_only"          # off | log_only | block

    def is_local_endpoint(self) -> bool:
        """本地端点（jevos 等自建服务）无需 api_key"""
        return any(h in self.endpoint for h in ("127.0.0.1", "localhost", "::1"))

    def validate(self) -> list[str]:
        """判断层配置自洽性校验（正式模式启动时调用；demo 不拦）"""
        errors = []
        if self.provider not in ("rule", "jev"):
            errors.append(f"judgment: 未知 provider: {self.provider}（rule|jev）")
        if self.redline_gate not in ("off", "log_only", "block"):
            errors.append(f"judgment: 未知 redline_gate: {self.redline_gate}（off|log_only|block）")
        if self.provider == "jev" and not self.mock:
            # 云端需要 key；本地开源判断端点（同一 wire 协议）不需要
            if not self.api_key and not self.is_local_endpoint():
                errors.append(
                    "judgment: provider=jev 且 mock=false，需配 api_key"
                    "或改 endpoint 为本地端点（127.0.0.1/localhost）")
        if self.redline_gate == "block" and self.provider != "jev":
            errors.append(
                "judgment: redline_gate=block 需要 provider=jev"
                "（规则后端做不了语义判断，诚实失败而非假装把关）")
        return errors


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
    mode: str = "demo"  # demo=占位/降级全放行（离线可跑）；production=严格校验+禁静默降级
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    judgment: JudgmentConfig = field(default_factory=JudgmentConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        path = Path(path)
        with open(path) as f:
            raw = yaml.safe_load(f)
        raw = _resolve_env_vars(raw)

        mode = str(raw.get("mode", "demo"))
        if mode not in ("demo", "production"):
            raise ValueError(f"未知 mode: {mode}（应为 demo 或 production）")
        budget_raw = raw.get("budget") or {}
        budget = BudgetConfig(
            per_shot_cny=budget_raw.get("per_shot_cny"),
            per_episode_cny=budget_raw.get("per_episode_cny"),
            project_cny=budget_raw.get("project_cny"),
        )
        judgment = JudgmentConfig(**{
            k: v for k, v in (raw.get("judgment") or {}).items()
            if k in {f.name for f in fields(JudgmentConfig)}
        })
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
            mode=mode,
            budget=budget,
            judgment=judgment,
        )

    def validate_production(self) -> list[str]:
        """正式模式启动前置校验。返回错误清单（空 = 通过）。

        原则（DEVELOPMENT_PLAN M1-1）：正式模式缺配置立即报错；
        未知价格不得记成免费（cost_per_call 必须显式 > 0）。
        """
        errors = []
        if self.llm.is_offline:
            errors.append("llm: 正式模式不能走离线模板（offline=true 或 api_key 为空）")
        for sub in ("text2img", "img2video"):
            api = self.apis[sub]
            if api.provider == "placeholder":
                errors.append(f"apis.{sub}: 正式模式不能用 placeholder provider")
            elif not api.api_key:
                errors.append(f"apis.{sub}: provider={api.provider} 但缺 api_key")
            if not api.cost_per_call or api.cost_per_call <= 0:
                errors.append(
                    f"apis.{sub}: cost_per_call 未配置或为 0"
                    f"（未知价格不得记成免费，须显式填写 ¥/次）")
        errors.extend(self.judgment.validate())
        return errors


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
