"""判断层（Jev 决策层）回归 — mock 接入、置信度阈值、回落链、红线闸门

对应 references/Jev决策层引入评估.md P1：
- provider=rule：判断层弃权，行为与历史完全一致（107 例既有回归即证明）
- provider=jev + mock=true：wire 格式保真的确定性模拟，验证接线/阈值/回落
- 真实 HTTP 路径：payload 结构与 answers 解析（mock 传输，不发网络）
- 失败链：Jev 异常 → 弃权 → 既有路径，绝不阻塞管线、绝不静默放行
"""

import httpx
import pytest

from drama.config import JudgmentConfig
from drama.executors.img2video import Img2VideoExecutor
from drama.judgment import Decision, JevDecisions, RuleDecisions, build_decisions
from drama.state import new_episode_state, new_shot_state

from conftest import make_orchestrator


def _jev_orch(tmp_path, max_retry=None, **overrides):
    """构建 provider=jev（mock）的 orchestrator（重新构建 decisions 客户端）"""
    orch, proj = make_orchestrator(tmp_path, max_retry=max_retry)
    orch.config.judgment.provider = "jev"
    for k, v in overrides.items():
        setattr(orch.config.judgment, k, v)
    orch.decisions = build_decisions(orch.config.judgment)
    return orch, proj


# ---------- 后端构建与配置校验 ----------

class TestBackendBuild:
    def test_rule_backend_abstains_everything(self):
        """provider=rule：三决策点全部弃权 → Orchestrator 走既有路径（行为不变）"""
        d = RuleDecisions()
        assert d.parse_review_intent("通过", {}).decided is False
        assert d.escalation_resolution(new_shot_state("s")).decided is False
        assert d.redline_risk("剧本", {}).decided is False

    def test_build_factory(self, tmp_path):
        assert isinstance(build_decisions(JudgmentConfig(provider="rule")), RuleDecisions)
        assert isinstance(build_decisions(JudgmentConfig(provider="jev")), JevDecisions)
        with pytest.raises(ValueError, match="provider"):
            build_decisions(JudgmentConfig(provider="gpt"))

    def test_config_validate_matrix(self):
        assert JudgmentConfig().validate() == []                       # rule 默认全通过
        assert JudgmentConfig(provider="jev", mock=False).validate() != []  # 云端缺 key
        assert JudgmentConfig(provider="jev", mock=False,
                              endpoint="http://127.0.0.1:8017").validate() == []  # 本地免 key
        assert JudgmentConfig(provider="jev", mock=False,
                              api_key="k").validate() == []
        errs = JudgmentConfig(redline_gate="block").validate()          # block+rule 诚实失败
        assert any("provider=jev" in e for e in errs)
        assert JudgmentConfig(provider="wat").validate() != []
        assert JudgmentConfig(redline_gate="sometimes").validate() != []


# ---------- mock 后端（wire 格式保真） ----------

class TestJevMock:
    def test_intent_approve_with_targets(self):
        j = JevDecisions(JudgmentConfig(provider="jev", mock=True))
        d = j.parse_review_intent("通过，没问题", {"episode": "ep01"})
        assert d.decided and d.value["decision"] == "approve"
        assert d.confidence >= 0.9 and d.backend == "jev-mock"
        assert d.value["raw"] == "通过，没问题"

    def test_intent_reject_extracts_shot_targets(self):
        j = JevDecisions(JudgmentConfig(provider="jev", mock=True))
        d = j.parse_review_intent("打回 镜头03 手不对", {})
        assert d.value["decision"] == "reject"
        assert d.value["targets"] == ["3"]      # 与规则解析同契约（去前导零）

    def test_intent_unclear_abstains(self):
        """模型确定看不懂 → 弃权（走既有保守复核），不盲拒也不放行"""
        j = JevDecisions(JudgmentConfig(provider="jev", mock=True))
        d = j.parse_review_intent("嗯……再想想吧", {})
        assert d.decided is False

    def test_escalation_by_error_class_and_type(self):
        j = JevDecisions(JudgmentConfig(provider="jev", mock=True))
        sh = new_shot_state("s", shot_type="simple")
        sh["img2video"]["error_class"] = "auth"
        assert j.escalation_resolution(sh).value == "manual"       # 配置类 → 人工
        sh2 = new_shot_state("s", shot_type="complex")
        assert j.escalation_resolution(sh2).value == "simplify"    # 复杂镜头 → 简化
        sh3 = new_shot_state("s", shot_type="simple")
        sh3["img2video"]["error_class"] = "timeout"
        d = j.escalation_resolution(sh3)
        assert d.value == "downgrade" and d.confidence >= 0.9

    def test_redline_keyword_screen(self):
        j = JevDecisions(JudgmentConfig(provider="jev", mock=True))
        d = j.redline_risk("他举起刀，血浆溅了满墙，断肢散落", {})
        assert d.value is True and d.confidence >= 0.9
        d2 = j.redline_risk("月光洒在庭院，她抬头望月", {})
        assert d2.value is False

    def test_parse_answer_wire_shapes(self):
        f = JevDecisions._parse_answer
        d = f({"type": "noul", "noul": 0.42})
        assert d.value is False and d.confidence == pytest.approx(0.58)
        d = f({"type": "choice", "choice": "skip",
               "probabilities": {"skip": 0.7, "manual": 0.2}})
        assert d.value == "skip" and d.confidence == pytest.approx(0.7)
        assert f(None).decided is False
        assert f({}).decided is False


# ---------- 真实 wire 路径（mock 传输，不发网络） ----------

class TestJevWire:
    def test_real_transport_payload_and_parse(self, monkeypatch):
        """mock=false：POST {endpoint}/v1/systemone，payload 含 model/state/questions"""
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured.update({"url": url, "json": json, "headers": headers})
            return httpx.Response(200, json={"answers": {
                "redline": {"type": "noul", "noul": 0.97}}},
                request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx, "post", fake_post)
        cfg = JudgmentConfig(provider="jev", mock=False, api_key="test-key")
        j = JevDecisions(cfg)
        d = j.redline_risk("血腥剧本", {"episode": "ep01"})

        assert d.value is True and d.confidence == pytest.approx(0.97)
        assert d.backend == "jev"
        assert captured["url"] == "https://api.typesafe.ai/v1/systemone"
        assert captured["headers"]["Authorization"] == "Bearer test-key"
        assert captured["json"]["model"] == "jev-latest"
        assert "血腥剧本" in captured["json"]["state"]["script_text"]
        assert captured["json"]["questions"]["redline"]["type"] == "noul"

    def test_transport_error_falls_back_to_abstain(self, monkeypatch):
        """回落链：Jev 任何异常 → 弃权（jev-error），调用方走既有路径"""
        def boom(*a, **k):
            raise httpx.ConnectTimeout("network down")

        monkeypatch.setattr(httpx, "post", boom)
        cfg = JudgmentConfig(provider="jev", mock=False, api_key="k")
        j = JevDecisions(cfg)
        d = j.redline_risk("剧本", {})
        assert d.decided is False and d.backend == "jev-error"
        assert j.escalation_resolution(new_shot_state("s")).decided is False
        assert j.parse_review_intent("通过", {}).decided is False


# ---------- Orchestrator 接线 ----------

class TestOrchestratorWiring:
    def test_rule_provider_escalation_unchanged(self, tmp_path, monkeypatch):
        """provider=rule：升级仍走 director agent（既有路径，行为不变）"""
        from drama.agents.director import DirectorAgent

        orch, proj = make_orchestrator(tmp_path, max_retry={
            "text2img": 2, "img2video_simple": 1,
            "img2video_complex": 2, "lip_sync": 1})
        assert isinstance(orch.decisions, RuleDecisions)
        called = []
        orig = DirectorAgent.run
        monkeypatch.setattr(DirectorAgent, "run",
                            lambda self, ctx: (called.append(1), orig(self, ctx))[1])
        monkeypatch.setattr(Img2VideoExecutor, "run",
                            lambda self, task: {"success": False, "error": "mock 失败"})
        orch.init_states("ep01")
        orch.run(episode_filter="ep01")
        s = orch.state_mgr.load("ep01")
        assert called, "rule 后端必须保留 director agent 升级路径"
        assert all(sh["img2video"]["status"] == "escalated" for sh in s["shots"])

    def test_jev_mock_decides_escalation_without_llm(self, tmp_path, monkeypatch):
        """provider=jev(mock)：高置信处置直接落地，director agent 不再被调用"""
        from drama.agents.director import DirectorAgent

        orch, proj = _jev_orch(tmp_path, max_retry={
            "text2img": 2, "img2video_simple": 1,
            "img2video_complex": 2, "lip_sync": 1})
        called = []
        monkeypatch.setattr(DirectorAgent, "run",
                            lambda self, ctx: called.append(1) or (_ for _ in ()).throw(
                                AssertionError("不应调用 director LLM")))
        monkeypatch.setattr(Img2VideoExecutor, "run",
                            lambda self, task: {"success": False, "error": "mock 失败"})
        orch.init_states("ep01")
        orch.run(episode_filter="ep01")
        s = orch.state_mgr.load("ep01")
        assert not called
        assert all(sh["img2video"]["status"] == "escalated" for sh in s["shots"])
        assert all("jev-mock" in (sh["img2video"].get("qa_notes") or "")
                   for sh in s["shots"])

    def test_redline_gate_log_only_warns_but_proceeds(self, tmp_path, offline_audio,
                                                      monkeypatch):
        orch, proj = _jev_orch(tmp_path, redline_gate="log_only")
        sent = []
        monkeypatch.setattr(orch.notifier, "send", lambda m: sent.append(m))
        orch.init_states("ep01")
        state = orch.state_mgr.load("ep01")
        script = proj.get_path("scripts") / "ep01.md"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("血浆四溅，断肢满地。", encoding="utf-8")
        state["script"].update(status="approved", file="03_剧本/ep01.md")
        orch.state_mgr.save("ep01", state)

        assert orch._redline_gate_ok("ep01", state) is True
        s = orch.state_mgr.load("ep01")
        assert s["script"]["redline_check"]["value"] is True          # 决策已审计
        assert any("红线" in m for m in sent)
        assert s["director_review"]["status"] == "pending"            # 未拦截

    def test_redline_gate_block_rejects_before_storyboard(self, tmp_path):
        orch, proj = _jev_orch(tmp_path, redline_gate="block")
        orch.init_states("ep01")
        state = orch.state_mgr.load("ep01")
        script = proj.get_path("scripts") / "ep01.md"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("血浆四溅，断肢满地。", encoding="utf-8")
        state["script"].update(status="approved", file="03_剧本/ep01.md")
        orch.state_mgr.save("ep01", state)

        assert orch._redline_gate_ok("ep01", state) is False
        s = orch.state_mgr.load("ep01")
        assert s["director_review"]["status"] == "rejected"
        assert "redline" in (s["director_review"].get("notes") or "")
        assert s["storyboard"]["status"] == "pending"                 # 未进分镜（未烧钱）

    def test_redline_gate_idempotent(self, tmp_path, monkeypatch):
        """闸门幂等：已查过不重查（决策记录复用）"""
        orch, proj = _jev_orch(tmp_path, redline_gate="log_only")
        calls = []
        monkeypatch.setattr(orch.decisions, "redline_risk",
                            lambda *a, **k: calls.append(1) or Decision(True, 0.93, "jev-mock"))
        orch.init_states("ep01")
        state = orch.state_mgr.load("ep01")
        state["script"].update(status="approved", file=None,
                               redline_check={"value": False, "confidence": 0.9,
                                              "backend": "jev-mock"})
        orch.state_mgr.save("ep01", state)
        assert orch._redline_gate_ok("ep01", state) is True
        assert not calls                                              # 复用已有决策

    def test_parse_review_high_confidence_uses_jev(self, tmp_path):
        orch, _ = _jev_orch(tmp_path)
        verdict = orch._parse_review("通过，没问题", "package", episode="ep01")
        assert verdict["decision"] == "approve"

    def test_parse_review_low_confidence_falls_back(self, tmp_path):
        """阈值抬高 → mock approve(0.95) 也不采信 → 走规则保守解析"""
        orch, _ = _jev_orch(tmp_path, min_confidence=0.99)
        verdict = orch._parse_review("通过，没问题", "package", episode="ep01")
        assert verdict["decision"] == "approve"          # 规则解析同一结论（来源不同）
        assert "jev" not in str(verdict.get("backend", ""))

    def test_parse_review_jev_error_falls_back(self, tmp_path, monkeypatch):
        """Jev 异常 → 弃权 → 既有规则路径（回落链 e2e）"""
        orch, _ = _jev_orch(tmp_path)
        monkeypatch.setattr(orch.decisions, "parse_review_intent",
                            lambda *a, **k: Decision.abstain("jev-error", reason="boom"))
        verdict = orch._parse_review("打回 镜头03", "package", episode="ep01")
        assert verdict["decision"] == "reject" and verdict["targets"] == ["3"]
