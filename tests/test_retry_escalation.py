"""重试预算 / 升级路径 / 全镜头失败的安全降级 — ARCHITECTURE §六 约束的回归"""

from drama.state import new_episode_state, new_shot_state

from conftest import make_orchestrator


class TestBudgetLogic:
    def test_budget_by_shot_type(self, tmp_path):
        """重试预算按镜头 type 取值：img2video 不得复用 text2img 预算"""
        orch, _ = make_orchestrator(tmp_path, max_retry={
            "text2img": 5, "img2video_simple": 5,
            "img2video_complex": 8, "lip_sync": 3,
        })
        max_retry = orch.project.production.get("max_retry", {})
        mk = lambda t: new_shot_state("s", shot_type=t)
        assert orch._budget_for("text2img", mk("simple"), max_retry) == 5
        assert orch._budget_for("img2video", mk("simple"), max_retry) == 5
        assert orch._budget_for("img2video", mk("complex"), max_retry) == 8
        assert orch._budget_for("img2video", mk("lip_sync"), max_retry) == 3

    def test_plan_shot_action_escalates_when_exhausted(self, tmp_path):
        """耗尽预算 → 升级 Action（升级路径可达性的纯逻辑验证）"""
        orch, _ = make_orchestrator(tmp_path)
        max_retry = {"text2img": 2, "img2video_simple": 2}
        state = new_episode_state(1, "幕")

        sh = new_shot_state("ep01_shot01", shot_type="simple")
        sh["text2img"]["status"] = "approved"   # i2v 判定前提：先有图
        sh["img2video"]["status"] = "qa_fail"
        sh["img2video"]["attempts"] = 2
        act = orch._plan_shot_action("ep01", state, sh, max_retry)
        assert act is not None
        assert act.type == "agent" and act.name == "director"
        assert act.extra["reason"] == "img2video_escalation"

    def test_plan_shot_action_within_budget_executes(self, tmp_path):
        orch, _ = make_orchestrator(tmp_path)
        max_retry = {"text2img": 2, "img2video_simple": 2}
        state = new_episode_state(1, "幕")
        sh = new_shot_state("ep01_shot01", t2i_prompt="p")
        sh["text2img"]["status"] = "approved"
        sh["img2video"]["attempts"] = 1
        act = orch._plan_shot_action("ep01", state, sh, max_retry)
        assert act is not None and act.type == "executor"
        assert act.name == "img2video"

    def test_escalated_shot_is_terminal(self, tmp_path):
        """escalated 是镜头终态：不再产生任何动作（收敛前提）"""
        orch, _ = make_orchestrator(tmp_path)
        state = new_episode_state(1, "幕")
        sh = new_shot_state("ep01_shot01", shot_type="simple")
        sh["img2video"]["status"] = "escalated"
        state["shots"].append(sh)
        assert orch._all_shots_done(state) is True
        assert orch._plan_shots("ep01", state, {}) == []


class TestEscalationE2E:
    def test_single_shot_failure_escalates_and_episode_converges(
            self, tmp_path, offline_audio, monkeypatch):
        """单镜头 i2v 连续失败 → 耗尽预算 → 升级 escalated → 整集仍能收敛出片"""
        from drama.executors.img2video import Img2VideoExecutor

        orch, proj = make_orchestrator(tmp_path, max_retry={
            "text2img": 2, "img2video_simple": 1,
            "img2video_complex": 2, "lip_sync": 1,
        })
        orig_run = Img2VideoExecutor.run

        def flaky(self, task):
            if task["shot_id"].endswith("shot01"):
                return {"success": False, "error": "mock: 生成持续失败"}
            return orig_run(self, task)

        monkeypatch.setattr(Img2VideoExecutor, "run", flaky)
        orch.init_states("ep01")
        orch.run(episode_filter="ep01")

        s = orch.state_mgr.load("ep01")
        by_id = {sh["id"]: sh for sh in s["shots"]}
        assert len(s["shots"]) >= 2, "需要多镜头才能区分升级与正常路径"

        bad = by_id["ep01_shot01"]
        assert bad["img2video"]["status"] == "escalated"
        assert bad["img2video"]["attempts"] == 1   # 预算 1：失败一次即升级
        assert bad["img2video"]["qa_notes"]        # 升级处理有留痕

        for sid, sh in by_id.items():
            if sid != "ep01_shot01":
                assert sh["img2video"]["status"] == "approved", sid

        # 升级不阻塞整集：audio/compose/终审照常走完（escalated 镜头不进成片）
        assert s["audio"]["status"] == "approved"
        assert s["composite"]["status"] == "approved"
        assert s["director_review"]["status"] == "approved"
        out = proj.get_path("output") / "ep01.mp4"
        assert out.exists() and out.stat().st_size > 0


class TestAllShotsFail:
    def test_all_shots_escalate_terminates_without_false_success(
            self, tmp_path, offline_audio, monkeypatch):
        """全镜头 i2v 失败升级（compose 0 片段）：明确整集失败终态，不误报完成。

        M1 起：全镜头终态但 0 个 approved → 立即写 failed 终态
        （composite/director_review=failed），不再依赖"停滞检测中止"兜底，
        也不空跑 audio/compose。
        """
        from drama.executors.img2video import Img2VideoExecutor

        orch, proj = make_orchestrator(tmp_path, max_retry={
            "text2img": 2, "img2video_simple": 1,
            "img2video_complex": 1, "lip_sync": 1,
        })
        monkeypatch.setattr(
            Img2VideoExecutor, "run",
            lambda self, task: {"success": False, "error": "mock: 全部失败"})

        orch.init_states("ep01")
        orch.run(episode_filter="ep01")

        s = orch.state_mgr.load("ep01")
        assert s["shots"], "应有结构化镜头"
        assert all(sh["img2video"]["status"] == "escalated" for sh in s["shots"])
        # 显式整集失败终态（取代旧的停滞中止）
        assert s["composite"]["status"] == "failed"
        assert s["director_review"]["status"] == "failed"
        assert "无可合成片段" in (s["composite"].get("error") or "")
        out = proj.get_path("output") / "ep01.mp4"
        assert not out.exists()
        # 前置环节不受牵连：t2i 正常；audio 不再空跑（无片可合，快速失败）
        assert all(sh["text2img"]["status"] == "approved" for sh in s["shots"])
        assert s["audio"]["status"] == "pending"


class TestT2iFailure:
    def test_text2img_failure_retries_then_escalates(
            self, tmp_path, offline_audio, monkeypatch):
        """t2i 失败路径：qa_fail → 重试耗尽 → 升级，且不进入 i2v（必须先有图）"""
        from drama.executors.text2img import Text2ImgExecutor

        orch, proj = make_orchestrator(tmp_path, max_retry={
            "text2img": 1, "img2video_simple": 2,
            "img2video_complex": 2, "lip_sync": 1,
        })
        monkeypatch.setattr(
            Text2ImgExecutor, "run",
            lambda self, task: {"success": False, "error": "mock: t2i 失败"})

        orch.init_states("ep01")
        orch.run(episode_filter="ep01")

        s = orch.state_mgr.load("ep01")
        for sh in s["shots"]:
            assert sh["text2img"]["status"] == "escalated", sh["id"]
            assert sh["text2img"]["attempts"] == 1
            assert sh["img2video"]["status"] == "pending"   # 无图不产视频
        assert s["composite"]["status"] != "approved"
