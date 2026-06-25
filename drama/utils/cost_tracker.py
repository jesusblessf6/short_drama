"""成本追踪模块

累计所有任务的实际消耗，输出成本报告。
"""

import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


class CostTracker:
    """成本追踪器"""

    # 创意/质检层（消耗 token 的环节）
    LLM_STAGES = {"writer", "storyboard", "visual_qa", "director", "discovery"}
    # 生成层（按调用/秒计费的环节）
    GEN_STAGES = {"text2img", "img2video", "audio", "compose"}

    def __init__(self, log_dir: str | Path):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.costs = defaultdict(lambda: {
            "llm_tokens": 0,
            "llm_cost_cny": 0.0,
            "api_calls": 0,
            "api_cost_cny": 0.0,
        })

    def record_llm(self, agent_name: str, tokens: int, cost_cny: float = 0.0) -> None:
        """记录 LLM token 消耗（及其折算金额）"""
        self.costs[agent_name]["llm_tokens"] += tokens
        self.costs[agent_name]["llm_cost_cny"] += cost_cny

    def record_api(self, executor_name: str, cost_cny: float) -> None:
        """记录 API 调用成本"""
        self.costs[executor_name]["api_calls"] += 1
        self.costs[executor_name]["api_cost_cny"] += cost_cny

    def _component_cost(self, c: dict) -> float:
        return c.get("llm_cost_cny", 0.0) + c.get("api_cost_cny", 0.0)

    def summary(self) -> dict:
        """生成成本汇总"""
        total_tokens = sum(c["llm_tokens"] for c in self.costs.values())
        total_api_calls = sum(c["api_calls"] for c in self.costs.values())
        total_cny = sum(self._component_cost(c) for c in self.costs.values())

        return {
            "breakdown": dict(self.costs),
            "total_llm_tokens": total_tokens,
            "total_api_calls": total_api_calls,
            "total_cny": round(total_cny, 2),
            "generated_at": datetime.now().isoformat(),
        }

    def cost_shares(self) -> tuple[dict, float]:
        """各环节成本占比（LLM 各环节合并为 llm 组）。返回 (shares, total_cny)"""
        groups = {"llm": 0.0, "text2img": 0.0, "img2video": 0.0,
                  "audio": 0.0, "compose": 0.0}
        for name, c in self.costs.items():
            cost = self._component_cost(c)
            if name in self.LLM_STAGES:
                groups["llm"] += cost
            elif name in groups:
                groups[name] += cost
        total = sum(groups.values())
        shares = {k: (v / total if total else 0.0) for k, v in groups.items()}
        return shares, total

    def token_shares(self) -> tuple[dict, int]:
        """LLM 各环节 token 占比。返回 (shares, total_tokens)"""
        toks = {n: self.costs[n]["llm_tokens"]
                for n in self.costs if n in self.LLM_STAGES}
        total = sum(toks.values())
        shares = {k: (v / total if total else 0.0) for k, v in toks.items()}
        return shares, total

    def check_deviation(self, baseline_cost: dict, baseline_token: dict,
                        threshold: float) -> list[str]:
        """实际占比 vs 基线，相对偏离超阈值则产出预警条目。无数据时不报。"""
        alerts = []
        cshares, ctotal = self.cost_shares()
        if ctotal > 0:
            for stage, base in baseline_cost.items():
                actual = cshares.get(stage, 0.0)
                if base > 0 and abs(actual - base) / base > threshold:
                    alerts.append(
                        f"成本占比偏离: {stage} 实际{actual:.0%} vs 基线{base:.0%}")
        tshares, ttotal = self.token_shares()
        if ttotal > 0:
            for stage, base in baseline_token.items():
                actual = tshares.get(stage, 0.0)
                if base > 0 and abs(actual - base) / base > threshold:
                    alerts.append(
                        f"token占比偏离: {stage} 实际{actual:.0%} vs 基线{base:.0%}")
        return alerts

    def save_report(self, filename: str = "cost_report.yaml") -> Path:
        """保存成本报告到文件"""
        report = self.summary()
        path = self.log_dir / filename
        with open(path, "w") as f:
            yaml.dump(report, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        logger.info(f"成本报告已保存: {path}")
        return path

    def log(self) -> str:
        """返回可读的成本日志"""
        s = self.summary()
        lines = [f"成本汇总: 总token={s['total_llm_tokens']} 总API调用={s['total_api_calls']} 总费用=¥{s['total_cny']}"]
        for name, cost in s["breakdown"].items():
            cny = cost.get("llm_cost_cny", 0.0) + cost.get("api_cost_cny", 0.0)
            lines.append(f"  {name}: token={cost['llm_tokens']} calls={cost['api_calls']} ¥{cny:.2f}")
        return "\n".join(lines)
