"""state.py 单元测试 — 状态契约与断点续跑基础"""

import pytest

from drama.state import (StateManager, new_episode_state, new_shot_state)


@pytest.fixture
def mgr(tmp_path):
    return StateManager(tmp_path / ".state")


class TestNewState:
    def test_episode_state_shape(self):
        s = new_episode_state(3, "某幕")
        assert s["episode"] == "ep03"
        assert s["episode_num"] == 3
        assert s["script"]["status"] == "pending"
        assert s["storyboard"]["status"] == "pending"
        assert s["shots"] == []
        assert s["audio"]["status"] == "pending"
        assert s["composite"]["status"] == "pending"
        assert s["director_review"]["status"] == "pending"
        assert s["cost_summary"] == {"llm_tokens": 0, "api_calls": 0, "cost_cny": 0.0}

    def test_shot_state_shape(self):
        sh = new_shot_state("ep01_shot01", scene="内堂", shot_type="complex",
                            t2i_prompt="p1", i2v_prompt="p2",
                            dialogue="台词", speaker="角色甲", duration=5)
        assert sh["type"] == "complex"
        assert sh["text2img"]["status"] == "pending"
        assert sh["img2video"]["status"] == "pending"
        assert sh["speaker"] == "角色甲"
        assert sh["duration"] == 5


class TestStateManager:
    def test_load_missing_returns_none(self, mgr):
        assert mgr.load("ep99") is None

    def test_update_task_unknown_task_raises(self, mgr):
        mgr.init_episode(1, "幕")
        with pytest.raises(ValueError):
            mgr.update_task("ep01", "nonexistent", status="approved")

    def test_update_task_missing_episode_raises(self, mgr):
        with pytest.raises(ValueError):
            mgr.update_task("ep404", "script", status="approved")

    def test_update_shot_unknown_shot_raises(self, mgr):
        mgr.init_episode(1, "幕")
        with pytest.raises(ValueError):
            mgr.update_shot("ep01", "ep01_shot99", "text2img", status="approved")

    def test_add_cost_accumulates(self, mgr):
        mgr.init_episode(1, "幕")
        mgr.add_cost("ep01", llm_tokens=100, cost_cny=0.2)
        mgr.add_cost("ep01", llm_tokens=50, api_calls=2, cost_cny=1.0)
        cs = mgr.load("ep01")["cost_summary"]
        assert cs["llm_tokens"] == 150
        assert cs["api_calls"] == 2
        assert cs["cost_cny"] == 1.2

    def test_add_cost_missing_episode_is_noop(self, mgr):
        mgr.add_cost("ep404", llm_tokens=1)   # 不抛异常（容错设计）

    def test_add_shot_and_update_shot(self, mgr):
        mgr.init_episode(1, "幕")
        shot = new_shot_state("ep01_shot01", t2i_prompt="p")
        mgr.add_shot("ep01", shot)
        assert len(mgr.load("ep01")["shots"]) == 1
        mgr.update_shot("ep01", "ep01_shot01", "text2img",
                        status="approved", attempts=1)
        sh = mgr.load("ep01")["shots"][0]
        assert sh["text2img"]["status"] == "approved"
        assert sh["text2img"]["attempts"] == 1


class TestResetInterrupted:
    def test_generating_reset_to_pending(self, mgr):
        """断点续跑核心：进程中断遗留的 generating/drafting 必须重置。

        M1 起 reset_interrupted 返回变更标志（bool，True=有修改），修改就地生效。
        """
        state = new_episode_state(1, "幕")
        shot = new_shot_state("ep01_shot01")
        shot["text2img"]["status"] = "generating"
        shot["img2video"]["status"] = "generating"
        state["shots"].append(shot)
        state["script"]["status"] = "drafting"
        state["storyboard"]["status"] = "drafting"

        mgr.save("ep01", state)
        reloaded = mgr.load("ep01")
        changed = mgr.reset_interrupted(reloaded)

        assert changed is True
        assert reloaded["script"]["status"] == "pending"
        assert reloaded["storyboard"]["status"] == "pending"
        assert reloaded["shots"][0]["text2img"]["status"] == "pending"
        assert reloaded["shots"][0]["img2video"]["status"] == "pending"


class TestShotSurfacing:
    def test_find_pending_shot_surfaces_exhausted(self, mgr):
        """耗尽重试的镜头也必须被 surface（升级路径可达的前提）"""
        state = new_episode_state(1, "幕")
        sh = new_shot_state("ep01_shot01")
        sh["img2video"]["status"] = "qa_fail"
        sh["img2video"]["attempts"] = 99
        state["shots"].append(sh)

        found = mgr.find_pending_shot(state)
        assert found is not None and found["id"] == "ep01_shot01"

    def test_all_shots_approved(self, mgr):
        state = new_episode_state(1, "幕")
        assert mgr.all_shots_approved(state) is False   # 空镜头不算通过
        sh = new_shot_state("ep01_shot01")
        sh["img2video"]["status"] = "approved"
        state["shots"].append(sh)
        assert mgr.all_shots_approved(state) is True
