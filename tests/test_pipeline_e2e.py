"""离线端到端 / 断点续跑 / 阶段过滤 / 人审流程 — M0 核心回归

全部在 tmp_path 隔离项目中运行，audio 走 ffmpeg 静音轨（无网络）。
"""

from conftest import make_orchestrator


def _run_to_end(orch, ep="ep01", **kw):
    orch.init_states(ep)
    orch.run(episode_filter=ep, **kw)
    return orch.state_mgr.load(ep)


class TestOfflineE2E:
    def test_full_pipeline_auto_mode(self, tmp_path, offline_audio):
        """零外部依赖端到端：init → 剧本 → 分镜 → 图 → 视频 → 配音 → 合成 → 终审"""
        orch, proj = make_orchestrator(tmp_path)   # 默认 director: auto
        state = _run_to_end(orch)

        assert state["script"]["status"] == "approved"
        assert state["storyboard"]["status"] == "approved"
        assert state["shots"], "离线分镜必须产出结构化镜头"
        for shot in state["shots"]:
            assert shot["text2img"]["status"] == "approved", shot["id"]
            assert shot["img2video"]["status"] == "approved", shot["id"]
        assert state["audio"]["status"] == "approved"
        assert state["composite"]["status"] == "approved"
        assert state["director_review"]["status"] == "approved"

        # 产物真实存在且非空（失败不得误报完成）
        out = proj.get_path("output") / "ep01.mp4"
        assert out.exists() and out.stat().st_size > 0
        for shot in state["shots"]:
            img = proj.project_root / shot["text2img"]["file"]
            vid = proj.project_root / shot["img2video"]["file"]
            assert img.exists() and img.stat().st_size > 0
            assert vid.exists() and vid.stat().st_size > 0

        # 中间产物文件路径与状态记录一致
        assert (proj.project_root / state["script"]["file"]).exists()
        assert (proj.project_root / state["storyboard"]["file"]).exists()

        # 成本记账：t2i + i2v + audio + compose 至少各记一笔
        assert state["cost_summary"]["api_calls"] >= len(state["shots"]) * 2 + 2

    def test_rerun_completed_episode_is_noop(self, tmp_path, offline_audio):
        """已完成集重跑：不得重复执行任何环节（断点续跑语义）"""
        orch, proj = make_orchestrator(tmp_path)
        state = _run_to_end(orch)
        attempts_before = {
            "script": state["script"]["attempts"],
            "storyboard": state["storyboard"]["attempts"],
            "shots": {sh["id"]: (sh["text2img"]["attempts"],
                                 sh["img2video"]["attempts"])
                      for sh in state["shots"]},
        }

        orch.run(episode_filter="ep01")     # 第二次全量跑
        state2 = orch.state_mgr.load("ep01")
        assert state2["script"]["attempts"] == attempts_before["script"]
        assert state2["storyboard"]["attempts"] == attempts_before["storyboard"]
        for sh in state2["shots"]:
            assert (sh["text2img"]["attempts"], sh["img2video"]["attempts"]) \
                == attempts_before["shots"][sh["id"]]
        assert state2["director_review"]["status"] == "approved"


class TestResume:
    def test_stage_filter_runs_single_stage(self, tmp_path, offline_audio):
        """--stage 语义：只跑指定环节，后续环节不动"""
        orch, proj = make_orchestrator(tmp_path)
        orch.init_states("ep01")

        orch.run(episode_filter="ep01", stage_filter="script")
        s = orch.state_mgr.load("ep01")
        assert s["script"]["status"] == "approved"
        assert s["storyboard"]["status"] == "pending"
        assert s["shots"] == []

        orch.run(episode_filter="ep01", stage_filter="storyboard")
        s = orch.state_mgr.load("ep01")
        assert s["storyboard"]["status"] == "approved"
        assert s["shots"], "分镜通过后应有结构化镜头"
        assert all(sh["text2img"]["status"] == "pending" for sh in s["shots"])

    def test_incremental_resume_completes(self, tmp_path, offline_audio):
        """分段执行后全量续跑：只补缺的环节，已完成的不再重做"""
        orch, proj = make_orchestrator(tmp_path)
        orch.init_states("ep01")
        orch.run(episode_filter="ep01", stage_filter="script")
        orch.run(episode_filter="ep01", stage_filter="storyboard")
        orch.run(episode_filter="ep01")     # 补完镜头/音频/合成/终审

        s = orch.state_mgr.load("ep01")
        assert s["script"]["attempts"] == 1
        assert s["storyboard"]["attempts"] == 1
        assert s["composite"]["status"] == "approved"
        assert s["director_review"]["status"] == "approved"

    def test_interrupted_generating_is_reset(self, tmp_path, offline_audio):
        """进程中断遗留 generating → 重启自动重置为 pending 并续跑"""
        orch, proj = make_orchestrator(tmp_path)
        orch.init_states("ep01")
        orch.run(episode_filter="ep01", stage_filter="script")
        orch.run(episode_filter="ep01", stage_filter="storyboard")

        # 模拟中断：第一镜头 t2i 卡在 generating
        orch.state_mgr.update_shot("ep01", "ep01_shot01", "text2img",
                                   status="generating")
        orch.run(episode_filter="ep01")

        s = orch.state_mgr.load("ep01")
        assert s["composite"]["status"] == "approved"
        assert all(sh["text2img"]["status"] == "approved" for sh in s["shots"])


class TestReviewMode:
    def test_review_mode_hangs_then_approve(self, tmp_path, offline_audio):
        """review 模式：合成通过后挂起等人审 → 提交"通过" → 续跑 → approved"""
        orch, proj = make_orchestrator(
            tmp_path, stage_modes={"director": "review"})
        orch.init_states("ep01")
        orch.run(episode_filter="ep01")

        s = orch.state_mgr.load("ep01")
        assert s["composite"]["status"] == "approved"    # 成片已出
        assert s["director_review"]["status"] == "reviewing"
        # 待审包已推送（文件通道）
        assert (proj.get_path("state") / "reviews" / "ep01__director.pending.txt").exists()

        orch.review_channel.submit("ep01:director", "通过，画面可以")
        orch.run(episode_filter="ep01")
        s = orch.state_mgr.load("ep01")
        assert s["director_review"]["status"] == "approved"
        assert "通过" in (s["director_review"]["notes"] or "")

    def test_review_mode_reject_then_reset(self, tmp_path, offline_audio):
        """打回 → rejected（含镜头定位）→ --reset-review 复活 → 重新送审"""
        orch, proj = make_orchestrator(
            tmp_path, stage_modes={"director": "review"})
        orch.init_states("ep01")
        orch.run(episode_filter="ep01")
        assert orch.state_mgr.load("ep01")["director_review"]["status"] == "reviewing"

        orch.review_channel.submit("ep01:director", "打回 镜头02 手不对")
        orch.run(episode_filter="ep01")
        s = orch.state_mgr.load("ep01")
        assert s["director_review"]["status"] == "rejected"
        assert "手不对" in (s["director_review"]["notes"] or "")

        # 复活：rejected → pending，残留清理
        assert orch.reset_review("ep01") is True
        s = orch.state_mgr.load("ep01")
        assert s["director_review"]["status"] == "pending"

        # 复活后重跑 → 重新进入 reviewing（可再次提交）
        orch.run(episode_filter="ep01")
        assert orch.state_mgr.load("ep01")["director_review"]["status"] == "reviewing"

    def test_conservative_reply_rejects(self, tmp_path, offline_audio):
        """模糊回复（无明确"通过"）→ 保守判 reject，不误放行"""
        orch, proj = make_orchestrator(
            tmp_path, stage_modes={"director": "review"})
        orch.init_states("ep01")
        orch.run(episode_filter="ep01")
        orch.review_channel.submit("ep01:director", "画面凑合吧，再说")
        orch.run(episode_filter="ep01")
        assert orch.state_mgr.load("ep01")["director_review"]["status"] == "rejected"
