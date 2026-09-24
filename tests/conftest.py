"""M0 测试基线 — 共享 fixture

原则（DEVELOPMENT_PLAN.md M0 验收标准）：
- 一切发生在 tmp_path 的隔离测试项目中，绝不读写 projects/三官
- 离线回归不访问任何外部服务：LLM 走离线模板（offline: true + 空 key），
  视觉走 placeholder provider，audio 的 edge_tts 被 monkeypatch 掉（走 ffmpeg 静音轨）
"""

import os
import sys
from pathlib import Path

import pytest
import yaml

# 保证从仓库根直接跑 pytest 时 drama 可导入（pip install -e . 之后其实不需要）
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def make_global_config(tmp_path: Path) -> Path:
    """生成零外部依赖的全局 config.yaml"""
    cfg = {
        "llm": {
            "provider": "volcengine",
            "base_url": "https://example.invalid/api",
            "api_key": "",           # 空 key + offline → is_offline，走离线模板
            "model": "glm-latest",
            "vision_model": "glm-4v",
            "max_tokens": 512,
            "temperature": 0.7,
            "offline": True,
            "price_per_1k_tokens": 0.002,
        },
        "apis": {
            "text2img": {"provider": "placeholder", "api_key": "",
                          "model": "placeholder", "cost_per_call": 0.0},
            "img2video": {"provider": "placeholder", "api_key": "",
                           "model": "placeholder", "cost_per_call": 0.0},
            "audio": {"provider": "edge_tts", "voice": "zh-CN-XiaoxiaoNeural"},
        },
        "orchestrator": {
            "parallel_episodes": 2, "parallel_shots": 3,
            "tick_interval": 0, "state_dir": ".state",
        },
        "notify": {"telegram": {"bot_token": "", "chat_id": ""}},
        "ffmpeg": {"path": os.environ.get("DRAMA_TEST_FFMPEG", "ffmpeg"),
                    "default_codec": "libx264", "default_crf": 30},
        # 测试中关闭成本偏离预警（离线成本全 0，开着只产生噪声）
        "cost_monitor": {"enabled": False},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(cfg, allow_unicode=True), encoding="utf-8")
    return path


def make_project(
    tmp_path: Path,
    *,
    name: str = "测试项目",
    acts: tuple = (("第一幕_测试", 1, 1),),
    stage_modes: dict | None = None,
    max_retry: dict | None = None,
) -> Path:
    """生成最小可跑的隔离项目目录，返回项目根"""
    root = tmp_path / "proj"
    for d in ("01_框架", "02_人物", "03_剧本", "04_分镜", "05_美术/风格定调",
              "06_音频", "07_成片", "08_质检", "09_制作日志", "reference"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "01_框架" / "整体框架.md").write_text("# 测试框架\n两幕结构。", encoding="utf-8")
    (root / "02_人物" / "角色甲.md").write_text("# 角色甲\n测试角色卡。", encoding="utf-8")
    (root / "05_美术" / "风格定调" / "风格.md").write_text("# 风格\n古风写实测试。", encoding="utf-8")

    production = {
        "shots_per_episode": "15-20",
        # 默认预算收紧，让重试/升级路径测试不用 mock 很多次
        "max_retry": max_retry or {"text2img": 2, "img2video_simple": 2,
                                    "img2video_complex": 2, "lip_sync": 2},
    }
    if stage_modes:
        production["stage_modes"] = stage_modes

    proj_yaml = {
        "name": name,
        "source_work": "测试作品",
        "source_author": "测试作者",
        "source_dynasty": "现代",
        "episodes": 1,
        "episode_duration": "1min",
        "aspect_ratio": "9:16",
        "paths": {
            "framework": "01_框架/整体框架.md", "characters": "02_人物/",
            "scripts": "03_剧本/", "storyboards": "04_分镜/", "art": "05_美术/",
            "audio": "06_音频/", "output": "07_成片/", "qa": "08_质检/",
            "logs": "09_制作日志/", "state": ".state/", "reference": "reference/",
        },
        "acts": [{"name": n, "episodes": [s, e]} for (n, s, e) in acts],
        "production": production,
    }
    (root / "project.yaml").write_text(
        yaml.dump(proj_yaml, allow_unicode=True), encoding="utf-8")
    return root


def make_orchestrator(tmp_path: Path, **project_kw):
    """构建隔离的 (Orchestrator, ProjectConfig)"""
    from drama.config import Config, ProjectConfig
    from drama.orchestrator import Orchestrator

    cfg = Config.from_yaml(make_global_config(tmp_path))
    root = make_project(tmp_path, **project_kw)
    proj = ProjectConfig.from_yaml(root / "project.yaml")
    return Orchestrator(cfg, proj), proj


@pytest.fixture
def offline_audio(monkeypatch):
    """把 edge_tts 打桩为失败 → AudioExecutor 走 ffmpeg 静音轨降级。

    保证测试确定性：不联网、不依赖 edge-tts 服务可用性。
    签名对齐 M2-5 的 _edge_tts(self, lines, output_path, config, voice_map) -> (bool, timings)。
    """
    from drama.executors.audio import AudioExecutor
    monkeypatch.setattr(AudioExecutor, "_edge_tts",
                        lambda self, lines, out, cfg, voice_map: (False, []))
