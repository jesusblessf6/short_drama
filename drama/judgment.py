"""判断层（M2+ 评估引入，见 references/Jev决策层引入评估.md）

Jev（TypeSafe AI "System One"）式类型化决策的接入层：输入结构化状态 + 类型化问题，
返回带置信度的决策。判断只服务 Orchestrator 的调度决策点（人审意图解析、升级处置、
红线预检），不进 Agents（创意生成）、不进 Executors（执行）。

后端：
- RuleDecisions  — 现行行为：全部弃权，由既有路径处理（provider=rule 默认，零 key）
- JevDecisions   — TypeSafe Jev 云 API 或本地开源替代（jevos 等，同一 wire 协议）。
  mock=true 时不发网络请求，用确定性启发式模拟响应（严格按 wire 格式，
  让解析/阈值/回落代码真实受测）；换本地模型只需改 endpoint，协议不变。

失败链（绝不阻塞管线、绝不静默放行）：
    Jev 异常/超时/解析失败 → 弃权 → Orchestrator 走既有路径；
    置信度 < min_confidence → 视同弃权，走既有路径（LLM 复核 / 人工）。

wire 协议（TypeSafe 兼容，见 jevos README）：
    POST {endpoint}/v1/systemone
    {"model": "jev-latest", "state": <text|json>,
     "questions": {"<name>": {"type": "noul|choice", "instructions": "...", "choices": [...]}}}
    → {"answers": {"<name>": {"type": "noul", "noul": 0.9}
                 | {"type": "choice", "choice": "manual", "probabilities": {...}}}}
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# 升级处置的合法取值（与 director prompt 的 escalation_resolution 一致）
ESCALATION_CHOICES = ["simplify", "downgrade", "skip", "manual"]

# 镜头号提取（与 review.py 的规则一致；人审回复里的"镜头03/shot3/第3个"）
_SHOT_RE = re.compile(r"(?:shot|镜头|第)\s*0*(\d+)")

# 红线预检关键词（mock 后端的确定性启发式；真实 Jev 做语义判断，不依赖此表）
_REDLINE_KEYWORDS = (
    "血腥", "断肢", "血浆", "自杀", "自残", "毒品", "制爆", "枪决", "色情",
)


@dataclass
class Decision:
    """一次类型化决策。value=None 表示弃权（调用方走既有路径）。"""

    value: Any = None
    confidence: float = 0.0
    backend: str = "rule"          # rule | jev-mock | jev | jev-error
    raw: Any = None

    @classmethod
    def abstain(cls, backend: str = "rule", reason: Any = None) -> "Decision":
        return cls(value=None, confidence=0.0, backend=backend, raw=reason)

    @property
    def decided(self) -> bool:
        return self.value is not None

    def to_dict(self) -> dict:
        """写入状态文件的审计形态（精简，raw 截断）"""
        return {"value": self.value, "confidence": round(self.confidence, 4),
                "backend": self.backend}


class DecisionClient:
    """判断层协议。三个决策点，返回 Decision；弃权 = value None。"""

    def parse_review_intent(self, reply: str, context: dict) -> Decision:
        raise NotImplementedError

    def escalation_resolution(self, shot: dict) -> Decision:
        raise NotImplementedError

    def redline_risk(self, script_text: str, context: dict) -> Decision:
        raise NotImplementedError


class RuleDecisions(DecisionClient):
    """规则后端 = 现行行为：全部弃权，判断层不介入。

    有意的空实现——现有正则解析/LLM 复核/升级路径已覆盖 provider=rule 的语义，
    引入一层"规则判断"只会复制逻辑制造双份真相。provider=jev 才是判断层的激活态。
    """

    def parse_review_intent(self, reply: str, context: dict) -> Decision:
        return Decision.abstain("rule")

    def escalation_resolution(self, shot: dict) -> Decision:
        return Decision.abstain("rule")

    def redline_risk(self, script_text: str, context: dict) -> Decision:
        return Decision.abstain("rule")


def build_decisions(cfg) -> DecisionClient:
    """按 config.judgment.provider 构建判断客户端。provider=rule（默认）→ 弃权后端。"""
    provider = getattr(cfg, "provider", "rule")
    if provider == "jev":
        return JevDecisions(cfg)
    if provider != "rule":
        raise ValueError(f"未知 judgment provider: {provider}（应为 rule 或 jev）")
    return RuleDecisions()


class JevDecisions(DecisionClient):
    """Jev 决策客户端（mock 或真实 wire 协议）。

    mock 响应用确定性启发式生成（关键词/错误类别），验证接线、置信度阈值与
    回落链——不代表真实 Jev 的语义判断质量，接通前不用于生产拦截。
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.backend = "jev-mock" if cfg.mock else "jev"

    # ---------- wire 协议 ----------

    def _ask(self, state: Any, questions: dict) -> dict:
        """返回 answers dict。mock 走本地启发式；真实走 HTTP。异常向上抛。"""
        if self.cfg.mock:
            return self._mock_answers(state, questions)
        import httpx
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}
        resp = httpx.post(
            f"{self.cfg.endpoint.rstrip('/')}/v1/systemone",
            json={"model": self.cfg.model, "state": state, "questions": questions},
            headers=headers, timeout=self.cfg.timeout,
        )
        resp.raise_for_status()
        return resp.json().get("answers", {})

    def _decide(self, state: Any, questions: dict) -> Decision:
        """ask + 解析 + 异常吞掉转弃权（回落链的统一入口）"""
        name = next(iter(questions))
        try:
            answers = self._ask(state, questions)
        except Exception as e:   # 网络/协议任何异常都不阻塞管线
            logger.warning(f"judgment({name}) 请求失败，弃权走既有路径: {e}")
            return Decision.abstain("jev-error", reason=str(e))
        d = self._parse_answer(answers.get(name))
        if d.decided:
            d.backend = self.backend   # jev-mock / jev（审计可区分模拟与真实）
        return d

    @staticmethod
    def _parse_answer(answer: dict | None) -> Decision:
        """wire answers → Decision。noul=P(yes)；choice 取概率表最大置信。"""
        if not answer or not isinstance(answer, dict):
            return Decision.abstain("jev-parse", reason=answer)
        if "noul" in answer:
            p = float(answer["noul"])
            return Decision(value=p >= 0.5, confidence=max(p, 1 - p),
                            backend="jev", raw=answer)
        if "choice" in answer:
            value = answer["choice"]
            probs = answer.get("probabilities") or {}
            conf = float(probs.get(value) or answer.get("confidence") or 0.0)
            return Decision(value=value, confidence=conf, backend="jev", raw=answer)
        return Decision.abstain("jev-parse", reason=answer)

    # ---------- 三个决策点 ----------

    def parse_review_intent(self, reply: str, context: dict) -> Decision:
        state = {
            "episode": (context or {}).get("episode", ""),
            "review_stage": "director",
            "reply": reply,
        }
        d = self._decide(state, {
            "intent": {
                "type": "choice",
                "choices": ["approve", "reject", "unclear"],
                "instructions": (
                    "审核员对短剧成片终审的回复。approve=通过本集；"
                    "reject=打回（附 targets 镜头号）；unclear=无法确定意图。"),
            },
        })
        if not d.decided:
            return d
        # choice → verdict dict（调用方契约：{decision, targets, reason, raw}）。
        # unclear = 模型确定看不懂 → 弃权，交既有路径保守复核（不直接放行也不盲拒）。
        if d.value == "unclear":
            return Decision.abstain(self.backend, reason=d.raw)
        targets = [m for m in (g.group(1) for g in _SHOT_RE.finditer(reply or ""))]
        return Decision(
            value={"decision": d.value, "targets": targets,
                   "reason": reply, "raw": reply},
            confidence=d.confidence, backend=d.backend, raw=d.raw)

    def escalation_resolution(self, shot: dict) -> Decision:
        t2i, i2v = shot.get("text2img", {}), shot.get("img2video", {})
        state = {
            "shot_id": shot.get("id"),
            "shot_type": shot.get("type"),
            "text2img_error": t2i.get("error_class"),
            "img2video_error": i2v.get("error_class"),
            "attempts": {"text2img": t2i.get("attempts"), "img2video": i2v.get("attempts")},
            "recent_errors": [e.get("error_class") for e in (i2v.get("attempts_log") or [])[-3:]],
        }
        return self._decide(state, {
            "resolution": {
                "type": "choice",
                "choices": ESCALATION_CHOICES,
                "instructions": (
                    "镜头生成反复失败的处置。simplify=简化动作重写分镜；"
                    "downgrade=降级静态图+旁白；skip=跳过非关键镜头；"
                    "manual=需人工干预。不确定时选 manual。"),
            },
        })

    def redline_risk(self, script_text: str, context: dict) -> Decision:
        state = {
            "episode": (context or {}).get("episode", ""),
            "script_text": script_text[:6000],   # jevos 上下文 8k，超长截断
        }
        return self._decide(state, {
            "redline": {
                "type": "noul",
                "instructions": (
                    "剧本是否触碰平台 AI 审核红线（血腥过程细节、自残自杀方法、"
                    "色情描写、可模仿违法细节、政治敏感）。P(yes)=触碰红线的概率。"),
            },
        })

    # ---------- mock 响应（确定性启发式，wire 格式保真） ----------

    def _mock_answers(self, state: Any, questions: dict) -> dict:
        name, q = next(iter(questions.items()))
        text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
        if q.get("type") == "noul":
            return {name: self._mock_noul(text)}
        return {name: self._mock_choice(name, state, q)}

    @staticmethod
    def _mock_noul(text: str) -> dict:
        hit = [k for k in _REDLINE_KEYWORDS if k in text]
        p = 0.93 if hit else 0.42
        return {"type": "noul", "noul": p, "hits": hit}

    def _mock_choice(self, name: str, state: Any, q: dict) -> dict:
        choices = q.get("choices") or []
        if name == "intent":
            reply = (state or {}).get("reply", "")
            low = reply.lower()
            if any(k in reply or k in low for k in ("打回", "重做", "重生成", "退回", "驳回")):
                return {"type": "choice", "choice": "reject",
                        "probabilities": {"reject": 0.9, "approve": 0.05, "unclear": 0.05}}
            if any(k in reply or k in low for k in ("通过", "同意", "过了", "ok", "approve", "👍")):
                return {"type": "choice", "choice": "approve",
                        "probabilities": {"approve": 0.95, "reject": 0.03, "unclear": 0.02}}
            return {"type": "choice", "choice": "unclear",
                    "probabilities": {"unclear": 0.45, "approve": 0.3, "reject": 0.25}}
        if name == "resolution":
            err = (state or {}).get("img2video_error") or (state or {}).get("text2img_error")
            probs = {c: 0.03 for c in choices}
            if err in ("auth", "param"):          # 配置类错误：必须人工
                probs["manual"] = 0.91
            elif (state or {}).get("shot_type") == "complex":
                probs["simplify"] = 0.92
            else:
                probs["downgrade"] = 0.92
            return {"type": "choice", "choice": max(probs, key=probs.get),
                    "probabilities": probs}
        return {"type": "choice", "choice": choices[0] if choices else "",
                "probabilities": {}}
