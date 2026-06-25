"""人审环节 — 回复通道 + 意图解析（轻量聊天式）

设计:
- 会话编排留在 Orchestrator(状态机);本模块只负责"收一句自然语言回复"和"把它解析成结构化结论"。
- 回复通道是可插拔 provider:先实现零依赖的 FileReviewChannel(文件注入),
  Telegram 等 IM 作为后续 provider,接口不变。
- 意图解析默认走规则(零依赖、离线可用);有真 LLM 时可升级(见 parse_with_llm)。
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------- 回复通道 ----------

class ReviewChannel:
    """收人审回复的通道抽象。

    一个待审项用 key 标识(如 "ep01:director")。provider 负责:
    - post(key, message): 把待审包推给人(可与 Notifier 重叠,通道也可只读)
    - poll(key) -> str | None: 取该项的人审回复文本,无则 None
    - ack(key): 消费掉该回复(避免重复处理)
    """

    def post(self, key: str, message: str) -> None:
        raise NotImplementedError

    def poll(self, key: str) -> str | None:
        raise NotImplementedError

    def ack(self, key: str) -> None:
        raise NotImplementedError

    def clear(self, key: str) -> None:
        """清掉该项的所有残留(待审/回复/已处理),供 reset 复活用。子类实现。"""
        raise NotImplementedError


class FileReviewChannel(ReviewChannel):
    """零依赖文件通道:回复写在 reply 目录下的文本文件里。

    - 待审推送:写一个 `<key>.pending.txt`(人或脚本看)
    - 人审回复:把回复写进 `<key>.reply.txt`(CLI `--review-reply` 或手动)
    - 消费:读到 reply 后改名为 `<key>.done.txt`
    key 中的 ':' 替换为 '__' 作文件名。
    """

    def __init__(self, reply_dir: str | Path):
        self.dir = Path(reply_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _f(self, key: str, suffix: str) -> Path:
        return self.dir / f"{key.replace(':', '__')}.{suffix}"

    def post(self, key: str, message: str) -> None:
        self._f(key, "pending.txt").write_text(message, encoding="utf-8")

    def submit(self, key: str, reply: str) -> None:
        """供 CLI/外部写入人审回复"""
        self._f(key, "reply.txt").write_text(reply, encoding="utf-8")

    def poll(self, key: str) -> str | None:
        f = self._f(key, "reply.txt")
        if f.exists():
            return f.read_text(encoding="utf-8").strip()
        return None

    def ack(self, key: str) -> None:
        reply = self._f(key, "reply.txt")
        if reply.exists():
            reply.rename(self._f(key, "done.txt"))
        self._f(key, "pending.txt").unlink(missing_ok=True)

    def clear(self, key: str) -> None:
        for suffix in ("pending.txt", "reply.txt", "done.txt"):
            self._f(key, suffix).unlink(missing_ok=True)


# ---------- 意图解析 ----------

_APPROVE_KW = ["通过", "同意", "可以", "没问题", "过了", "ok", "approve", "approved", "lgtm", "👍"]
_REJECT_KW = ["打回", "重做", "重画", "重生成", "不行", "退回", "驳回", "有问题", "reject", "redo"]
_SHOT_RE = re.compile(r"(?:shot|镜头|第)\s*0*(\d+)")


def parse_review_reply(text: str) -> dict:
    """把自然语言回复解析成结构化结论(规则版)。

    返回 {decision: approve|reject, targets: [shot_id片段...], reason, raw}。
    保守原则:识别不到明确"通过"就当 reject(审核宁可不误放行)。
    """
    raw = (text or "").strip()
    low = raw.lower()

    has_reject = any(k in raw or k in low for k in _REJECT_KW)
    has_approve = any(k in raw or k in low for k in _APPROVE_KW)

    targets = [m.group(1) for m in _SHOT_RE.finditer(raw)]

    if has_reject:
        decision = "reject"
    elif has_approve:
        decision = "approve"
    else:
        # 无明确信号:保守判 reject,把原文作为意见
        decision = "reject"

    return {
        "decision": decision,
        "targets": targets,
        "reason": raw,
        "raw": raw,
    }


def parse_with_llm(text: str, context: str, llm) -> dict:
    """有真 LLM 时的升级解析(应对'把台词改短点'这类复杂意图)。

    llm: 具 chat(messages) 的 LLMClient;离线则不应调用本函数。
    失败时回退规则解析。
    """
    try:
        import json
        prompt = (
            "你是短剧审核意图解析器。把审核员的自然语言回复解析为 JSON:\n"
            '{"decision":"approve或reject","targets":["镜头号如03"],"reason":"简述"}\n'
            f"审核上下文:{context}\n审核员回复:{text}\n只输出 JSON。"
        )
        resp = llm.chat([{"role": "user", "content": prompt}])
        m = re.search(r"\{.*\}", resp, re.DOTALL)
        if m:
            d = json.loads(m.group(0))
            d.setdefault("decision", "reject")
            d.setdefault("targets", [])
            d.setdefault("reason", text)
            d["raw"] = text
            return d
    except Exception as e:
        logger.warning(f"LLM 意图解析失败,回退规则: {e}")
    return parse_review_reply(text)
