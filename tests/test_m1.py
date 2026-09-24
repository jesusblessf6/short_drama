"""M1 回归 — demo/正式模式、状态扩展、恢复能力、预算闸门

对应 DEVELOPMENT_PLAN.md M1 验收：
- 正式模式缺配置立即报错；降级产物阻止自动通过
- 外部任务 ID / 尝试记录 / 错误类别 / 产物来源入状态，兼容旧 YAML
- 原子写、单项目运行锁、外部任务提交/轮询/恢复框架
- 可重试与不可重试错误分流；无有效片段明确失败终态
- 三级预算：提交前检查预估额度，耗尽停止新付费任务
"""

import yaml

from drama.config import Config
from drama.orchestrator import Action, _shot_done
from drama.state import (
    SHOT_STATUSES, TASK_STATUSES, StateManager, migrate_state,
    new_episode_state, new_shot_state,
)
from drama.utils.project_lock import ProjectLock
from drama.utils.retry import classify_exception, is_retryable

from conftest import make_orchestrator


# ---------- demo / production 模式 ----------

class TestModeValidation:
    def _production_ready(self, orch):
        c = orch.config
        c.mode = "production"
        c.llm.offline = False
        c.llm.api_key = "test-key"
        for sub, provider in (("text2img", "jimeng"), ("img2video", "kling")):
            c.apis[sub].provider = provider
            c.apis[sub].api_key = "test-key"
            c.apis[sub].cost_per_call = 0.5
        return orch

    def test_demo_mode_default(self, tmp_path):
        """默认 demo：零 key + placeholder 照常跑（离线可跑通承诺不破）"""
        orch, _ = make_orchestrator(tmp_path)
        assert orch.config.mode == "demo"
        assert orch.config.validate_production() != []   # demo 配置过不了正式校验（预期）

    def test_production_ok_when_fully_configured(self, tmp_path):
        orch, _ = make_orchestrator(tmp_path)
        self._production_ready(orch)
        assert orch.config.validate_production() == []

    def test_production_rejects_placeholder_provider(self, tmp_path):
        orch, _ = make_orchestrator(tmp_path)
        self._production_ready(orch)
        orch.config.apis["text2img"].provider = "placeholder"
        errors = orch.config.validate_production()
        assert any("placeholder" in e for e in errors)

    def test_production_rejects_missing_key(self, tmp_path):
        orch, _ = make_orchestrator(tmp_path)
        self._production_ready(orch)
        orch.config.apis["img2video"].api_key = ""
        errors = orch.config.validate_production()
        assert any("api_key" in e for e in errors)

    def test_production_rejects_unknown_price(self, tmp_path):
        """未知价格不得记成免费：cost_per_call=0 拒绝"""
        orch, _ = make_orchestrator(tmp_path)
        self._production_ready(orch)
        orch.config.apis["text2img"].cost_per_call = 0.0
        errors = orch.config.validate_production()
        assert any("cost_per_call" in e for e in errors)

    def test_production_rejects_offline_llm(self, tmp_path):
        orch, _ = make_orchestrator(tmp_path)
        self._production_ready(orch)
        orch.config.llm.api_key = ""
        errors = orch.config.validate_production()
        assert any("离线" in e for e in errors)

    def test_unknown_mode_rejected(self, tmp_path):
        from pathlib import Path
        cfg_path = tmp_path / "bad.yaml"
        cfg_path.write_text("mode: staging\nllm: {}\n")
        try:
            Config.from_yaml(cfg_path)
            assert False, "应抛 ValueError"
        except ValueError as e:
            assert "mode" in str(e)


class TestDegradedAudio:
    def _audio_action(self, orch):
        state = new_episode_state(1, "幕")
        orch.state_mgr.save("ep01", state)   # apply 走文件，须先落盘
        return Action("executor", "audio", "ep01", state), state

    def test_degraded_audio_approved_in_demo(self, tmp_path):
        """demo：静音降级放行，但状态显式标记 degraded + source"""
        orch, _ = make_orchestrator(tmp_path)
        action, _ = self._audio_action(orch)
        orch._apply_executor_result(action, {
            "success": True, "file": "/x/ep01.wav", "cost": 0.0,
            "source": "silent_fallback", "degraded": True})
        s = orch.state_mgr.load("ep01")
        assert s["audio"]["status"] == "approved"
        assert s["audio"]["degraded"] is True
        assert s["audio"]["source"] == "silent_fallback"

    def test_degraded_audio_fails_episode_in_production(self, tmp_path):
        """正式模式：降级产物不得自动通过 → audio failed + 整集失败终态"""
        orch, _ = make_orchestrator(tmp_path)
        orch.config.mode = "production"
        action, _ = self._audio_action(orch)
        orch._apply_executor_result(action, {
            "success": True, "file": "/x/ep01.wav", "cost": 0.0,
            "source": "silent_fallback", "degraded": True})
        s = orch.state_mgr.load("ep01")
        assert s["audio"]["status"] == "failed"
        assert s["director_review"]["status"] == "failed"
        assert s["composite"]["status"] == "failed"

    def test_real_audio_approved_in_production(self, tmp_path):
        """正式模式：非降级的真实配音正常通过"""
        orch, _ = make_orchestrator(tmp_path)
        orch.config.mode = "production"
        action, _ = self._audio_action(orch)
        orch._apply_executor_result(action, {
            "success": True, "file": "/x/ep01.wav", "cost": 0.0,
            "source": "edge_tts"})
        s = orch.state_mgr.load("ep01")
        assert s["audio"]["status"] == "approved"
        assert s["audio"].get("degraded") is False


class TestPlaceholderApprovedGuard:
    def _approved_ep(self, orch, *, t2i_source="jimeng", i2v_source="kling",
                     audio_source="edge_tts", audio_degraded=False):
        """构造一集 director approved 的状态，sources 可调"""
        state = new_episode_state(1, "幕")
        sh = new_shot_state("ep01_shot01")
        sh["text2img"].update(status="approved", file="a.png", source=t2i_source)
        sh["img2video"].update(status="approved", file="a.mp4", source=i2v_source)
        state["shots"].append(sh)
        state["audio"].update(status="approved", file="ep01.wav",
                              source=audio_source, degraded=audio_degraded)
        state["composite"].update(status="approved", file="ep01.mp4",
                                  source="ffmpeg")
        state["director_review"]["status"] = "approved"
        orch.state_mgr.save("ep01", state)
        return state

    def _stale(self, orch):
        return orch._placeholder_approved(orch.state_mgr.load_all(), None)

    def test_production_blocks_placeholder_approved_episode(
            self, tmp_path, offline_audio):
        """正式模式启动拦截：approved 集若产物来自占位链（试点死局）→ 拒跑并提示作废"""
        orch, _ = make_orchestrator(tmp_path)
        orch.init_states("ep01")
        orch.run(episode_filter="ep01")
        assert orch.state_mgr.load("ep01")["director_review"]["status"] == "approved"

        # 切正式模式 + 配齐 provider（模拟用户接真实服务后投产）
        c = orch.config
        c.mode = "production"
        c.llm.offline = False
        c.llm.api_key = "k"
        for sub, provider in (("text2img", "jimeng"), ("img2video", "kling")):
            c.apis[sub].provider = provider
            c.apis[sub].api_key = "k"
            c.apis[sub].cost_per_call = 0.5
        assert orch.config.validate_production() == []

        try:
            orch.run(episode_filter="ep01")
            assert False, "应抛 SystemExit"
        except SystemExit as e:
            assert "reset-episode" in str(e) and "ep01" in str(e)

        # 作废后即可正常启动（锁会释放、不再拦截）
        assert orch.reset_episode("ep01") is True
        stale = orch._placeholder_approved(orch.state_mgr.load_all(), "ep01")
        assert stale == []

    def test_gate_catches_silent_audio_chain(self, tmp_path):
        """评审 P2-2：真实镜头 + 静音降级 audio → 闸门必须拦截（demo→production 漏网路径）"""
        orch, _ = make_orchestrator(tmp_path)
        self._approved_ep(orch, audio_source="silent_fallback", audio_degraded=True)
        assert self._stale(orch) == ["ep01"]

    def test_gate_catches_legacy_audio_without_source(self, tmp_path):
        """旧状态 audio 无 source 字段：视为未知来源，拦截"""
        orch, _ = make_orchestrator(tmp_path)
        st = self._approved_ep(orch)
        st["audio"]["source"] = None
        orch.state_mgr.save("ep01", st)
        assert self._stale(orch) == ["ep01"]

    def test_gate_passes_fully_real_chain(self, tmp_path):
        """真实镜头 + 真实配音：不拦（闸门不误伤）"""
        orch, _ = make_orchestrator(tmp_path)
        self._approved_ep(orch, audio_source="edge_tts", audio_degraded=False)
        assert self._stale(orch) == []


# ---------- 状态扩展 ----------

class TestStateSchema:
    def test_migrate_legacy_state(self):
        """旧格式状态（无 M1 字段）加载后补全，已有值不动"""
        legacy = {
            "episode": "ep01", "episode_num": 1, "act": "幕",
            "script": {"status": "approved", "file": "a.md", "attempts": 1},
            "storyboard": {"status": "approved", "file": "b.md", "attempts": 1},
            "shots": [{
                "id": "ep01_shot01", "type": "simple", "duration": 4,
                "text2img": {"status": "approved", "file": "x.png",
                             "attempts": 1, "cost": 0.5},
                "img2video": {"status": "approved", "file": "x.mp4",
                              "attempts": 1, "cost": 2.0},
            }],
            "audio": {"status": "approved", "file": "ep01.wav"},
            "composite": {"status": "approved", "file": "ep01.mp4"},
            "director_review": {"status": "approved", "result": "approved"},
        }
        s = migrate_state(legacy)
        assert s["shots"][0]["text2img"]["source"] is None
        assert s["shots"][0]["img2video"]["attempts_log"] == []
        assert s["shots"][0]["text2img"]["cost"] == 0.5      # 原值保留
        assert s["script"]["source"] is None
        assert s["audio"]["degraded"] is False
        # 迁移后可直接被判为占位链（source=None）
        assert s["shots"][0]["text2img"]["external_task_id"] is None

    def test_failed_in_status_enums(self):
        assert "failed" in TASK_STATUSES
        assert "failed" in SHOT_STATUSES

    def test_shot_done_treats_failed_as_terminal(self):
        sh = new_shot_state("s")
        sh["text2img"]["status"] = "failed"
        assert _shot_done(sh)
        sh2 = new_shot_state("s")
        sh2["text2img"]["status"] = "approved"
        sh2["img2video"]["status"] = "failed"
        assert _shot_done(sh2)

    def test_atomic_write_leaves_no_tmp(self, tmp_path):
        """原子写：保存后无 .tmp 残留，文件内容完整可读"""
        mgr = StateManager(tmp_path / "state")
        mgr.init_episode(1, "幕")
        mgr.update_task("ep01", "script", status="approved")
        assert not list((tmp_path / "state").glob("*.tmp"))
        s = mgr.load("ep01")
        assert s["script"]["status"] == "approved"

    def test_reset_interrupted_keeps_external_task(self, tmp_path):
        """generating 且有外部任务 ID：保持原状（防重复付费提交）；无 ID → pending"""
        mgr = StateManager(tmp_path / "state")
        s = new_episode_state(1, "幕")
        sh = new_shot_state("ep01_shot01")
        sh["img2video"]["status"] = "generating"
        sh["img2video"]["external_task_id"] = "task-abc"
        s["shots"].append(sh)
        sh2 = new_shot_state("ep01_shot02")
        sh2["text2img"]["status"] = "generating"   # 无 task id
        s["shots"].append(sh2)
        mgr.reset_interrupted(s)
        assert s["shots"][0]["img2video"]["status"] == "generating"
        assert s["shots"][1]["text2img"]["status"] == "pending"

    def test_reset_interrupted_returns_change_flag(self, tmp_path):
        """评审 P3-2：无变更返回 False（调用方跳过落盘），有变更返回 True 且就地修改"""
        mgr = StateManager(tmp_path / "state")
        s = new_episode_state(1, "幕")
        sh = new_shot_state("ep01_shot01")
        sh["text2img"]["status"] = "approved"       # 干净状态
        sh["img2video"]["status"] = "generating"
        sh["img2video"]["external_task_id"] = "t1"  # 在途：不算变更
        s["shots"].append(sh)
        assert mgr.reset_interrupted(s) is False

        sh2 = new_shot_state("ep01_shot02")
        sh2["text2img"]["status"] = "generating"    # 无 ID 的 generating → pending
        s["shots"].append(sh2)
        assert mgr.reset_interrupted(s) is True
        assert s["shots"][1]["text2img"]["status"] == "pending"

    def test_reset_episode_clears_but_keeps_cost(self, tmp_path):
        """整集作废：环节回 pending、镜头清空；累计成本保留（钱已花，防反复作废烧钱）"""
        orch, _ = make_orchestrator(tmp_path)
        orch.init_states("ep01")
        orch.state_mgr.update_task("ep01", "script", status="approved",
                                   file="03_剧本/ep01.md")
        orch.state_mgr.add_shot("ep01", new_shot_state("ep01_shot01"))
        orch.state_mgr.add_cost("ep01", cost_cny=12.5, api_calls=5)
        assert orch.reset_episode("ep01") is True
        s = orch.state_mgr.load("ep01")
        assert s["script"]["status"] == "pending"
        assert s["script"]["file"] is None
        assert s["shots"] == []
        assert s["director_review"]["status"] == "pending"
        assert s["cost_summary"]["cost_cny"] == 12.5
        assert s["cost_summary"]["api_calls"] == 5


# ---------- 错误分类与失败终态 ----------

class TestErrorClassification:
    def test_classify_exception_matrix(self):
        import httpx
        assert classify_exception(httpx.ConnectTimeout("t")) == "timeout"
        assert classify_exception(NotImplementedError("todo")) == "param"
        assert classify_exception(RuntimeError("connection refused")) == "network"
        assert classify_exception(RuntimeError("HTTP 429 too many")) == "rate_limit"
        assert classify_exception(RuntimeError("401 unauthorized")) == "auth"
        assert classify_exception(RuntimeError("什么都不是")) == "unknown"

    def test_is_retryable_mapping(self):
        assert is_retryable("timeout") and is_retryable("rate_limit")
        assert is_retryable("network") and is_retryable("unknown")
        assert not is_retryable("auth") and not is_retryable("param")

    def _shot_action(self, orch):
        state = new_episode_state(1, "幕")
        sh = new_shot_state("ep01_shot01", shot_type="simple")
        sh["text2img"]["status"] = "approved"
        sh["text2img"]["file"] = "05_美术/shots/ep01_shot01.png"
        state["shots"].append(sh)
        return Action("executor", "img2video", "ep01", state, sh), state

    def test_fatal_error_goes_to_failed_terminal(self, tmp_path):
        """鉴权类失败：立即 failed 终态，不消耗重试预算"""
        orch, _ = make_orchestrator(tmp_path)
        orch.init_states("ep01")
        action, state = self._shot_action(orch)
        orch.state_mgr.save("ep01", state)
        orch._apply_executor_result(action, {
            "success": False, "error": "401 unauthorized",
            "error_class": "auth"})
        s = orch.state_mgr.load("ep01")
        v = s["shots"][0]["img2video"]
        assert v["status"] == "failed"
        assert v["attempts"] == 1
        assert v["error_class"] == "auth"
        assert len(v["attempts_log"]) == 1
        assert v["attempts_log"][0]["error_class"] == "auth"
        assert _shot_done(s["shots"][0])   # 终态：调度器不再派发

    def test_retryable_error_keeps_qa_fail(self, tmp_path):
        """网络类失败：保持 qa_fail 可重试（既有路径不变）"""
        orch, _ = make_orchestrator(tmp_path)
        orch.init_states("ep01")
        action, state = self._shot_action(orch)
        orch.state_mgr.save("ep01", state)
        orch._apply_executor_result(action, {
            "success": False, "error": "connection refused",
            "error_class": "network"})
        s = orch.state_mgr.load("ep01")
        v = s["shots"][0]["img2video"]
        assert v["status"] == "qa_fail"
        assert not _shot_done(s["shots"][0])

    def test_success_writes_source(self, tmp_path):
        """成功结果回写产物来源（占位作废/正式拦截的判据）"""
        orch, proj = make_orchestrator(tmp_path)
        orch.init_states("ep01")
        action, state = self._shot_action(orch)
        orch.state_mgr.save("ep01", state)
        out = proj.get_path("art") / "shots" / "ep01_shot01.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        orch._apply_executor_result(action, {
            "success": True, "file": str(out), "cost": 0.0,
            "source": "placeholder"})
        s = orch.state_mgr.load("ep01")
        v = s["shots"][0]["img2video"]
        assert v["status"] == "approved"
        assert v["source"] == "placeholder"
        assert v["error_class"] is None


# ---------- 外部任务提交/轮询/恢复 ----------

class TestExternalTaskRecovery:
    def _inflight_state(self):
        state = new_episode_state(1, "幕")
        sh = new_shot_state("ep01_shot01", shot_type="simple")
        sh["text2img"]["status"] = "approved"
        sh["text2img"]["file"] = "05_美术/shots/ep01_shot01.png"
        sh["img2video"]["status"] = "generating"
        sh["img2video"]["external_task_id"] = "kling-task-42"
        state["shots"].append(sh)
        return state, sh

    def test_submitted_task_stays_generating(self, tmp_path):
        """executor 报'已提交未完成'：保持 generating + 记任务ID，不算失败不耗 attempts"""
        orch, _ = make_orchestrator(tmp_path)
        state, sh = self._inflight_state()
        orch.state_mgr.save("ep01", state)
        action = Action("executor", "img2video", "ep01", state, sh)
        orch._apply_executor_result(action, {
            "success": False, "submitted": True,
            "external_task_id": "kling-task-42"})
        s = orch.state_mgr.load("ep01")
        v = s["shots"][0]["img2video"]
        assert v["status"] == "generating"
        assert v["external_task_id"] == "kling-task-42"
        assert v["attempts"] == 0
        assert v["attempts_log"] == []

    def test_plan_dispatches_poll_with_task_id(self, tmp_path):
        """恢复路径：在途任务 → 派发轮询动作，task 携带 external_task_id"""
        orch, _ = make_orchestrator(tmp_path)
        state, sh = self._inflight_state()
        act = orch._plan_shot_action("ep01", state, sh, {"img2video_simple": 5})
        assert act is not None and act.type == "executor"
        task = orch._build_executor_task(act)
        assert task["external_task_id"] == "kling-task-42"

    def test_generating_without_id_waits_for_reset(self, tmp_path):
        """无任务ID 的 generating（未及提交就中断）：不派发，等 reset_interrupted 归位"""
        orch, _ = make_orchestrator(tmp_path)
        state = new_episode_state(1, "幕")
        sh = new_shot_state("ep01_shot01", t2i_prompt="p")
        sh["text2img"]["status"] = "generating"    # 无 external_task_id
        state["shots"].append(sh)
        assert orch._plan_shot_action("ep01", state, sh, {}) is None

    def test_dispatch_records_generating_and_input_hash(self, tmp_path, monkeypatch):
        """派发前落 generating + 输入指纹（中断恢复与输入审计的凭据）"""
        from drama.executors.text2img import Text2ImgExecutor
        seen = {}
        orig = Text2ImgExecutor.run

        def spy(self, task):
            seen["task"] = task
            return orig(self, task)

        monkeypatch.setattr(Text2ImgExecutor, "run", spy)
        orch, _ = make_orchestrator(tmp_path)
        orch.init_states("ep01")
        state = orch.state_mgr.load("ep01")
        sh = new_shot_state("ep01_shot01", t2i_prompt="中景测试")
        state["shots"].append(sh)
        orch.state_mgr.save("ep01", state)
        action = Action("executor", "text2img", "ep01", state, sh)
        orch._execute_executor(action)
        v = orch.state_mgr.load("ep01")["shots"][0]["text2img"]
        assert v["status"] == "approved"          # 占位同步完成
        assert v["input_hash"]                    # 指纹已记
        assert v["source"] == "placeholder"
        assert seen["task"]["external_task_id"] is None


# ---------- 运行锁 ----------

class TestProjectLock:
    def test_double_acquire_fails(self, tmp_path):
        lock1 = ProjectLock(tmp_path / ".lock")
        lock1.acquire()
        lock2 = ProjectLock(tmp_path / ".lock")
        try:
            lock2.acquire()
            assert False, "应抛 RuntimeError"
        except RuntimeError as e:
            assert "运行中" in str(e)
        finally:
            lock1.release()

    def test_release_allows_reacquire(self, tmp_path):
        lock = ProjectLock(tmp_path / ".lock")
        lock.acquire()
        lock.release()
        ProjectLock(tmp_path / ".lock").acquire()   # 不抛即通过

    def test_cli_state_commands_respect_lock(self, tmp_path):
        """评审 P3-1：调度器持锁期间，写状态的 CLI 子命令拒绝执行（防并发写状态）"""
        from conftest import make_global_config, make_project
        from test_cli import _cli

        root = make_project(tmp_path)
        cfg = make_global_config(tmp_path)
        assert _cli("--init-episode", "ep01", config_path=cfg,
                    project_root=root).returncode == 0

        # 标记一个可观察状态，验证持锁期间 reset 未执行
        state_path = root / ".state" / "ep01.yaml"
        state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
        state["script"]["status"] = "approved"
        state_path.write_text(yaml.dump(state, allow_unicode=True), encoding="utf-8")

        lock = ProjectLock(root / ".state" / ".lock")
        lock.acquire()
        try:
            r = _cli("--reset-episode", "ep01", config_path=cfg, project_root=root)
            assert r.returncode == 0 and "运行中" in r.stdout
            assert yaml.safe_load(state_path.read_text(encoding="utf-8"))[
                "script"]["status"] == "approved"   # 未被重置
        finally:
            lock.release()

        r = _cli("--reset-episode", "ep01", config_path=cfg, project_root=root)
        assert "已整集作废重置" in r.stdout
        assert yaml.safe_load(state_path.read_text(encoding="utf-8"))[
            "script"]["status"] == "pending"


# ---------- 三级预算 ----------

class TestBudgetGate:
    def _setup(self, tmp_path, *, per_shot=None, per_episode=None, project=None):
        orch, _ = make_orchestrator(tmp_path)
        for sub in ("text2img", "img2video"):
            orch.config.apis[sub].cost_per_call = 10.0
        orch.config.budget.per_shot_cny = per_shot
        orch.config.budget.per_episode_cny = per_episode
        orch.config.budget.project_cny = project
        return orch

    def _state_with_shot(self, t2i_cost=0.0, ep_spent=0.0):
        state = new_episode_state(1, "幕")
        state["cost_summary"]["cost_cny"] = ep_spent
        sh = new_shot_state("ep01_shot01", shot_type="simple")
        sh["text2img"]["status"] = "approved"
        sh["text2img"]["cost"] = t2i_cost
        state["shots"].append(sh)
        return state, sh

    def test_free_tasks_never_blocked(self, tmp_path):
        """单价 0（占位/免费）：预算不生效——离线跑通承诺不破"""
        orch, _ = make_orchestrator(tmp_path)
        orch.config.budget.per_shot_cny = 0.01
        state, sh = self._state_with_shot()
        assert orch._generation_budget_block("ep01", state, sh, "img2video") is None

    def test_shot_level_budget_escalates(self, tmp_path):
        """镜头级超额 → 升级终态（该镜头烧完了）"""
        orch = self._setup(tmp_path, per_shot=15.0)
        state, sh = self._state_with_shot(t2i_cost=10.0)   # 10 + 10 > 15
        act = orch._plan_shot_action("ep01", state, sh, {"img2video_simple": 5})
        assert act.type == "agent" and act.name == "director"
        assert act.extra["reason"] == "img2video_budget_exhausted"

    def test_episode_level_budget_blocks_silently(self, tmp_path):
        """集级超额 → 不派发新付费任务（镜头保持 pending 等预算调整，不升级）"""
        orch = self._setup(tmp_path, per_episode=25.0)
        state, sh = self._state_with_shot(ep_spent=20.0)   # 20 + 10 > 25
        assert orch._plan_shot_action("ep01", state, sh, {"img2video_simple": 5}) is None

    def test_project_level_budget_blocks(self, tmp_path):
        orch = self._setup(tmp_path, project=25.0)
        state, sh = self._state_with_shot(ep_spent=20.0)
        orch.state_mgr.save("ep01", state)   # 项目级汇总读磁盘
        assert orch._plan_shot_action("ep01", state, sh, {"img2video_simple": 5}) is None

    def test_within_budget_dispatches(self, tmp_path):
        """预算内：正常派发（闸门不误伤）"""
        orch = self._setup(tmp_path, per_shot=30.0, per_episode=100.0)
        state, sh = self._state_with_shot(t2i_cost=10.0, ep_spent=15.0)
        act = orch._plan_shot_action("ep01", state, sh, {"img2video_simple": 5})
        assert act.type == "executor" and act.name == "img2video"

    def test_budget_block_never_sends_fake_success(self, tmp_path, monkeypatch):
        """评审 P2-1：预算耗尽挂起时，绝不能发"✅ 全部完成"假成功通知"""
        from drama.executors.text2img import Text2ImgExecutor

        orch = self._setup(tmp_path, per_episode=25.0)
        sent = []
        monkeypatch.setattr(orch.notifier, "send", lambda msg: sent.append(msg))
        orig = Text2ImgExecutor.run

        def paid(self, task):
            result = orig(self, task)
            result["cost"] = 10.0
            return result

        monkeypatch.setattr(Text2ImgExecutor, "run", paid)
        orch.init_states("ep01")
        orch.run(episode_filter="ep01")

        assert any("预算" in m for m in sent), f"应发预算挂起通知: {sent}"
        assert not any("全部完成" in m for m in sent), \
            f"挂起状态不得发假成功通知: {sent}"

    def test_budget_exhaustion_stops_new_paid_tasks(self, tmp_path, monkeypatch):
        """e2e：集预算耗尽 → 不再提交付费任务，镜头停在 pending（非升级终态），运行收敛"""
        from drama.executors.text2img import Text2ImgExecutor

        orch = self._setup(tmp_path, per_episode=25.0)
        calls = []
        orig = Text2ImgExecutor.run

        def paid(self, task):
            calls.append(task["shot_id"])
            result = orig(self, task)
            result["cost"] = 10.0        # 模拟付费 provider
            return result

        monkeypatch.setattr(Text2ImgExecutor, "run", paid)
        orch.init_states("ep01")
        orch.run(episode_filter="ep01")

        s = orch.state_mgr.load("ep01")
        # 前两次提交（10+10=20 ≤ 25 预估放行），第三次起 20+10 > 25 全部拦截
        assert len(calls) == 2
        for sh in s["shots"]:
            assert sh["text2img"]["status"] in ("approved", "pending")
            assert sh["text2img"]["status"] != "escalated"   # 预算≠失败，不升级
        assert s["audio"]["status"] == "pending"             # 镜头没完不往下走
        assert s["director_review"]["status"] == "pending"   # 不误报完成也不误报失败
