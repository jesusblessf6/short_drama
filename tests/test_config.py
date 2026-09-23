"""config.py 单元测试 — 环境变量替换 / 离线判定 / 项目配置容错"""

import textwrap

import pytest
import yaml

from drama.config import Config, ProjectConfig

from conftest import make_global_config, make_project


class TestLLMOffline:
    def test_explicit_offline_flag(self, tmp_path):
        p = tmp_path / "c.yaml"
        p.write_text(yaml.dump({
            "llm": {"provider": "x", "base_url": "u", "api_key": "SOMEKEY",
                     "model": "m", "vision_model": "v", "max_tokens": 1,
                     "temperature": 0.5, "offline": True},
            "apis": {
                "text2img": {"provider": "p", "api_key": "", "model": "m"},
                "img2video": {"provider": "p", "api_key": "", "model": "m"},
                "audio": {"provider": "edge_tts", "voice": "v"},
            },
            "orchestrator": {"parallel_episodes": 1, "parallel_shots": 1,
                              "tick_interval": 0, "state_dir": ".state"},
        }), encoding="utf-8")
        cfg = Config.from_yaml(p)
        assert cfg.llm.is_offline is True   # 显式 offline 优先

    def test_empty_key_implies_offline(self, tmp_path):
        """未配 key（含环境变量未设、${VAR} 替换为空）→ 自动离线"""
        p = tmp_path / "c.yaml"
        p.write_text(yaml.dump({
            "llm": {"provider": "x", "base_url": "u",
                     "api_key": "${NOT_SET_ENV_VAR_XYZ}",
                     "model": "m", "vision_model": "v", "max_tokens": 1,
                     "temperature": 0.5, "offline": False},
            "apis": {
                "text2img": {"provider": "p", "api_key": "", "model": "m"},
                "img2video": {"provider": "p", "api_key": "", "model": "m"},
                "audio": {"provider": "edge_tts", "voice": "v"},
            },
            "orchestrator": {"parallel_episodes": 1, "parallel_shots": 1,
                              "tick_interval": 0, "state_dir": ".state"},
        }), encoding="utf-8")
        cfg = Config.from_yaml(p)
        assert cfg.llm.api_key == ""        # 未设的环境变量替换为空
        assert cfg.llm.is_offline is True

    def test_key_present_means_online(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_ARK_KEY", "real-key")
        p = tmp_path / "c.yaml"
        p.write_text(yaml.dump({
            "llm": {"provider": "x", "base_url": "u",
                     "api_key": "${TEST_ARK_KEY}",
                     "model": "m", "vision_model": "v", "max_tokens": 1,
                     "temperature": 0.5, "offline": False},
            "apis": {
                "text2img": {"provider": "p", "api_key": "", "model": "m"},
                "img2video": {"provider": "p", "api_key": "", "model": "m"},
                "audio": {"provider": "edge_tts", "voice": "v"},
            },
            "orchestrator": {"parallel_episodes": 1, "parallel_shots": 1,
                              "tick_interval": 0, "state_dir": ".state"},
        }), encoding="utf-8")
        cfg = Config.from_yaml(p)
        assert cfg.llm.api_key == "real-key"
        assert cfg.llm.is_offline is False


class TestEnvSubstitution:
    def test_nested_env_vars_resolved(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_TOK", "tok-1")
        monkeypatch.setenv("TEST_CHAT", "chat-9")
        cfg_path = tmp_path / "c.yaml"
        cfg_path.write_text(textwrap.dedent("""
            llm: &base
              provider: p
              base_url: u
              api_key: "${TEST_TOK}"
              model: m
              vision_model: v
              max_tokens: 1
              temperature: 0.5
            apis:
              text2img: {provider: p, api_key: "", model: m}
              img2video: {provider: p, api_key: "", model: m}
              audio: {provider: edge_tts, voice: v}
            orchestrator: {parallel_episodes: 1, parallel_shots: 1, tick_interval: 0, state_dir: .state}
            notify:
              telegram: {bot_token: "${TEST_TOK}", chat_id: "${TEST_CHAT}"}
        """), encoding="utf-8")
        cfg = Config.from_yaml(cfg_path)
        assert cfg.notify.telegram_bot_token == "tok-1"
        assert cfg.notify.telegram_chat_id == "chat-9"


class TestProjectConfig:
    def test_unknown_fields_ignored(self, tmp_path):
        """project.yaml 多出未知字段不应 TypeError（回归：#5 修复）"""
        root = make_project(tmp_path)
        raw = yaml.safe_load((root / "project.yaml").read_text(encoding="utf-8"))
        raw["future_field"] = {"anything": 1}
        (root / "project.yaml").write_text(
            yaml.dump(raw, allow_unicode=True), encoding="utf-8")
        proj = ProjectConfig.from_yaml(root / "project.yaml")
        assert proj.name == "测试项目"

    def test_act_lookup(self, tmp_path):
        root = make_project(tmp_path, acts=(("A幕", 1, 3), ("B幕", 4, 6)))
        proj = ProjectConfig.from_yaml(root / "project.yaml")
        assert proj.get_act_for_episode(2) == "A幕"
        assert proj.get_act_for_episode(5) == "B幕"
        assert proj.get_episode_range("B幕") == (4, 6)
        with pytest.raises(ValueError):
            proj.get_act_for_episode(99)

    def test_get_path_absolute(self, tmp_path):
        root = make_project(tmp_path)
        proj = ProjectConfig.from_yaml(root / "project.yaml")
        p = proj.get_path("state")
        assert p.is_absolute() and p.name == ".state"

    def test_global_config_fixture_is_offline(self, tmp_path):
        """conftest 的全局配置必须是零外部依赖的"""
        cfg = Config.from_yaml(make_global_config(tmp_path))
        assert cfg.llm.is_offline
        assert cfg.apis["text2img"].provider == "placeholder"
        assert cfg.apis["img2video"].provider == "placeholder"
