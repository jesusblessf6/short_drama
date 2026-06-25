"""调度器 — 系统的核心状态机（重写版）

职责：
1. 读取所有集的状态文件
2. 判断每个任务当前阶段，决定下一步调谁
3. 派发任务到 Agent 或 Executor
4. 收集结果，更新状态（status 由本调度器决定，agent/executor 返回不含 status）
5. 串行执行（asyncio 并行为后续任务）
6. 断点续跑 + 停滞检测

契约见 ARCHITECTURE.md §12/§13/§14：
- context 必含 episode/episode_num/act/state（镜头任务另含 shot，升级另含 extra）
- agent result 不含 status；storyboard 返回结构化 shots（id/scene/type/t2i_prompt/i2v_prompt/...）
- 镜头初始化用 new_shot_state(...)；executor task 由本调度器构建（prompt/output_path 等）
- 重试预算按镜头 type 取；耗尽 → 升级 director；升级回写终态使集收敛
"""

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config, ProjectConfig
from .state import StateManager, new_shot_state
from .notify import Notifier
from .utils.cost_tracker import CostTracker
from .review import FileReviewChannel, parse_review_reply, parse_with_llm

logger = logging.getLogger(__name__)


@dataclass
class Action:
    """调度器产出的动作"""
    type: str            # "agent" | "executor"
    name: str            # agent/executor 名称
    episode: str         # 集ID
    state: dict          # 集状态
    shot: dict | None = None    # 镜头状态（镜头级任务用）
    extra: dict | None = None   # 额外参数（如升级 reason）


# 镜头终态（不再需要调度）：t2i 升级 → 无图无法成片；或 i2v 已通过/已升级
def _shot_done(shot: dict) -> bool:
    if shot["text2img"]["status"] == "escalated":
        return True
    return shot["img2video"]["status"] in ("approved", "escalated")


class Orchestrator:
    """调度器主类"""

    def __init__(self, config: Config, project: ProjectConfig):
        self.config = config
        self.project = project
        self.state_mgr = StateManager(project.get_path("state"))
        self.notifier = Notifier(config.notify)
        self.cost = CostTracker(project.get_path("logs"))
        # 人审:每环节模式(auto|review)在 project.yaml 的 production.stage_modes 配
        self.stage_modes = self.project.production.get("stage_modes", {})
        self.review_channel = FileReviewChannel(project.get_path("state") / "reviews")
        self.agents: dict = {}
        self.executors: dict = {}
        self._init_components()

    def _stage_mode(self, stage: str) -> str:
        return self.stage_modes.get(stage, "auto")

    # ---------- 组件加载 ----------

    def _init_components(self):
        """初始化 Agent 和 Executor，容错：未实现/导入失败的跳过"""
        from .agents.writer import WriterAgent
        from .agents.storyboard import StoryboardAgent
        from .agents.visual_qa import VisualQAAgent
        from .agents.director import DirectorAgent
        from .executors.text2img import Text2ImgExecutor
        from .executors.img2video import Img2VideoExecutor
        from .executors.audio import AudioExecutor
        from .executors.compose import ComposeExecutor

        agent_classes = {
            "writer": WriterAgent, "storyboard": StoryboardAgent,
            "visual_qa": VisualQAAgent, "director": DirectorAgent,
        }
        executor_classes = {
            "text2img": Text2ImgExecutor, "img2video": Img2VideoExecutor,
            "audio": AudioExecutor, "compose": ComposeExecutor,
        }
        for name, cls in agent_classes.items():
            try:
                self.agents[name] = cls(self.config)
            except Exception as e:
                logger.warning(f"Agent '{name}' 初始化失败: {e}")
        for name, cls in executor_classes.items():
            try:
                self.executors[name] = cls(self.config)
            except Exception as e:
                logger.warning(f"Executor '{name}' 初始化失败: {e}")

    # ---------- 状态初始化 ----------

    def init_states(self, only: str | None = None) -> int:
        """按 project.acts 初始化集状态文件。only 给定时只初始化该集（如 ep01）。"""
        created = 0
        for act in self.project.acts:
            name = act["name"]
            start, end = act["episodes"]
            for n in range(start, end + 1):
                ep = f"ep{n:02d}"
                if only and ep != only:
                    continue
                if self.state_mgr.load(ep) is not None:
                    logger.info(f"{ep} 状态已存在，跳过")
                    continue
                self.state_mgr.init_episode(n, name)
                created += 1
                logger.info(f"已初始化 {ep}（{name}）")
        logger.info(f"共初始化 {created} 集")
        return created

    # ---------- 主循环 ----------

    def run(self, episode_filter: str | None = None, stage_filter: str | None = None):
        logger.info(f"启动调度器 — 项目: {self.project.name}")
        self.notifier.send(f"🎬 短剧工厂启动 — 项目: {self.project.name}")

        prev_sig = None
        stall = 0
        while True:
            states = self.state_mgr.load_all()
            # 重置中断任务（generating/drafting → pending）
            for state in states:
                self.state_mgr.reset_interrupted(state)
                self.state_mgr.save(state["episode"], state)

            actions = self.plan_actions(states, episode_filter, stage_filter)
            if not actions:
                reviewing = [s["episode"] for s in states
                             if s["director_review"]["status"] == "reviewing"]
                rejected = [s["episode"] for s in states
                            if s["director_review"]["status"] == "rejected"]
                if reviewing:
                    logger.info(f"⏸ 等待人工审核: {reviewing}（--review-reply 提交后重跑）")
                    self.notifier.send(f"⏸ 等待人工审核: {', '.join(reviewing)}")
                elif rejected:
                    logger.info(f"↩️ 被打回待处理: {rejected} — 用 "
                                f"`--reset-review <ep>` 复活,或编辑状态文件决定重跑环节")
                    self.notifier.send(f"↩️ {', '.join(rejected)} 被打回,"
                                       f"用 --reset-review 复活")
                else:
                    logger.info("所有任务完成")
                    self.notifier.send(f"✅ 项目 '{self.project.name}' 当前可执行任务已全部完成")
                break

            # 停滞检测：动作存在但状态无进展，连续 3 轮 → 中止，避免死循环
            sig = self._progress_sig(states)
            if sig == prev_sig:
                stall += 1
                if stall >= 3:
                    logger.error("检测到停滞（有动作但无进展），中止")
                    self.notifier.send("🛑 调度停滞，已中止，请检查日志")
                    break
            else:
                stall = 0
            prev_sig = sig

            for action in actions:
                self.execute_action(action)

        self.cost.save_report()
        logger.info(self.cost.log())
        self._check_cost_deviation()

    def _progress_sig(self, states: list[dict]):
        """进度指纹：用于停滞检测"""
        sig = []
        for s in states:
            done = sum(1 for sh in s.get("shots", []) if _shot_done(sh))
            sig.append((
                s["episode"], s["script"]["status"], s["storyboard"]["status"],
                len(s.get("shots", [])), done,
                s["audio"]["status"], s["composite"]["status"],
                s["director_review"]["status"],
            ))
        return tuple(sig)

    # ---------- 规划 ----------

    def plan_actions(self, states, episode_filter=None, stage_filter=None) -> list[Action]:
        self._drain_review_replies(states)   # 先消费已到的人审回复
        actions = []
        max_retry = self.project.production.get("max_retry", {})
        for state in states:
            ep = state["episode"]
            if episode_filter and ep != episode_filter:
                continue
            if state["director_review"]["status"] == "approved":
                continue
            actions.extend(self._plan_episode(ep, state, max_retry, stage_filter))
        return self._apply_parallelism(actions)

    def _plan_episode(self, ep, state, max_retry, stage_filter) -> list[Action]:
        # 1 剧本
        if state["script"]["status"] != "approved":
            if self._stage_ok(stage_filter, "script") and "writer" in self.agents:
                return [Action("agent", "writer", ep, state)]
            return []
        # 2 分镜
        if state["storyboard"]["status"] != "approved":
            if self._stage_ok(stage_filter, "storyboard") and "storyboard" in self.agents:
                return [Action("agent", "storyboard", ep, state)]
            return []
        # 3 镜头生产
        if not self._all_shots_done(state):
            if self._stage_ok(stage_filter, "text2img", "img2video"):
                return self._plan_shots(ep, state, max_retry)
            return []
        # 4 配音
        if state["audio"]["status"] != "approved":
            if self._stage_ok(stage_filter, "audio") and "audio" in self.executors:
                return [Action("executor", "audio", ep, state)]
            return []
        # 5 合成
        if state["composite"]["status"] != "approved":
            if self._stage_ok(stage_filter, "compose") and "compose" in self.executors:
                return [Action("executor", "compose", ep, state)]
            return []
        # 6 终审
        dr = state["director_review"]["status"]
        if dr == "reviewing":
            return []   # 人审中,挂起等回复(由 _drain_review_replies 推进)
        if dr == "pending":
            if self._stage_mode("director") == "review":
                return [Action("review", "director", ep, state)]
            if self._stage_ok(stage_filter, "director") and "director" in self.agents:
                return [Action("agent", "director", ep, state)]
        return []

    def _plan_shots(self, ep, state, max_retry) -> list[Action]:
        """枚举可执行镜头（上限 parallel_shots）。结构支持并行，串行模式逐个执行。"""
        limit = max(1, self.config.orchestrator.parallel_shots)
        acts = []
        for shot in state.get("shots", []):
            if _shot_done(shot):
                continue
            a = self._plan_shot_action(ep, state, shot, max_retry)
            if a:
                acts.append(a)
            if len(acts) >= limit:
                break
        return acts

    def _plan_shot_action(self, ep, state, shot, max_retry) -> Action | None:
        """单镜头下一步：先 t2i 后 i2v；耗尽预算 → 升级 director。唯一的重试判定处。"""
        for sub in ("text2img", "img2video"):
            st = shot[sub]
            if st["status"] == "approved":
                continue
            if st["status"] == "escalated":
                return None  # 已升级为终态
            if sub == "img2video" and shot["text2img"]["status"] != "approved":
                return None  # 必须先有图
            budget = self._budget_for(sub, shot, max_retry)
            if st["attempts"] >= budget:
                return Action("agent", "director", ep, state, shot,
                              {"reason": f"{sub}_escalation"})
            return Action("executor", sub, ep, state, shot)
        return None

    def _budget_for(self, sub: str, shot: dict, max_retry: dict) -> int:
        if sub == "text2img":
            return int(max_retry.get("text2img", 5))
        t = shot.get("type", "simple")
        if t == "complex":
            return int(max_retry.get("img2video_complex", 8))
        if t == "lip_sync":
            return int(max_retry.get("lip_sync", 3))
        return int(max_retry.get("img2video_simple", 5))

    def _all_shots_done(self, state) -> bool:
        shots = state.get("shots", [])
        return bool(shots) and all(_shot_done(s) for s in shots)

    def _stage_ok(self, stage_filter, *stages) -> bool:
        return not stage_filter or stage_filter in stages

    def _apply_parallelism(self, actions: list[Action]) -> list[Action]:
        """集间并行上限（镜头级已在 _plan_shots 限制）"""
        max_ep = max(1, self.config.orchestrator.parallel_episodes)
        seen, result = [], []
        for a in actions:
            if a.episode not in seen:
                if len(seen) >= max_ep:
                    continue
                seen.append(a.episode)
            result.append(a)
        return result

    # ---------- 执行 ----------

    def execute_action(self, action: Action) -> None:
        logger.info(f"[{action.episode}] 执行: {action.type}/{action.name}"
                    + (f"/{action.shot['id']}" if action.shot else ""))
        try:
            if action.type == "agent":
                self._execute_agent(action)
            elif action.type == "executor":
                self._execute_executor(action)
            elif action.type == "review":
                self._execute_review(action)
        except Exception as e:
            logger.error(f"[{action.episode}] {action.name} 执行失败: {e}")
            self.notifier.send(f"⚠️ {action.episode} {action.name} 执行失败: {e}")

    def _execute_agent(self, action: Action) -> None:
        result = self.agents[action.name].run(self._build_agent_context(action))
        self._apply_agent_result(action, result)

    def _execute_executor(self, action: Action) -> None:
        result = self.executors[action.name].run(self._build_executor_task(action))
        self._apply_executor_result(action, result)

    # ---------- 人审(轻量聊天式) ----------

    def _execute_review(self, action: Action) -> None:
        """进入人审:写 reviewing + 推送待审包(不调 agent 橡皮图章)"""
        ep, name, state = action.episode, action.name, action.state
        key = f"{ep}:{name}"
        pkg = self._review_package(ep, state, name)
        self.review_channel.post(key, pkg)
        self.notifier.send(
            f"🔎 待人审 [{key}]\n{pkg}\n"
            f"回复: drama --project <项目> --review-reply {ep} {name} \"通过 / 打回 镜头03 ...\""
        )
        self.state_mgr.update_task(ep, "director_review", status="reviewing")
        state["director_review"]["status"] = "reviewing"

    def _review_package(self, ep: str, state: dict, name: str) -> str:
        shots = state.get("shots", [])
        return (
            f"{ep} 终审\n"
            f"成片: {state['composite'].get('file', '?')}\n"
            f"剧本: {state['script'].get('file', '?')}\n"
            f"分镜: {state['storyboard'].get('file', '?')} | 镜头数: {len(shots)}\n"
            f"请回复「通过」或「打回 [镜头号] 原因」"
        )

    def _drain_review_replies(self, states: list[dict]) -> None:
        """消费已到的人审回复 → 写 approved/rejected(内存+文件同步)"""
        for state in states:
            if state["director_review"]["status"] != "reviewing":
                continue
            ep = state["episode"]
            key = f"{ep}:director"
            reply = self.review_channel.poll(key)
            if reply is None:
                continue
            verdict = self._parse_review(reply, self._review_package(ep, state, "director"))
            self.review_channel.ack(key)
            if verdict["decision"] == "approve":
                self.state_mgr.update_task(ep, "director_review", status="approved",
                                           result="approved", notes=verdict["reason"])
                state["director_review"]["status"] = "approved"
                self.notifier.send(f"🎬 {ep} 人审通过")
            else:
                tgt = (" 镜头" + ",".join(verdict["targets"])) if verdict["targets"] else ""
                self.state_mgr.update_task(ep, "director_review", status="rejected",
                                           result="rejected", notes=verdict["reason"])
                state["director_review"]["status"] = "rejected"
                self.notifier.send(f"↩️ {ep} 人审打回{tgt}: {verdict['reason']}")

    def _parse_review(self, reply: str, context: str) -> dict:
        """离线/无 LLM 用规则解析;有真 LLM 升级"""
        if self.config.llm.is_offline or "director" not in self.agents:
            return parse_review_reply(reply)
        return parse_with_llm(reply, context, self.agents["director"].llm)

    def reset_review(self, episode: str) -> bool:
        """把被打回/审核中的集重新激活:director_review → pending,清理残留。

        解决"人审打回后集卡在 rejected、无复活入口"的问题。
        """
        state = self.state_mgr.load(episode)
        if state is None:
            logger.error(f"{episode} 状态不存在")
            return False
        old = state["director_review"]["status"]
        self.state_mgr.update_task(episode, "director_review",
                                   status="pending", result=None, notes=None)
        # 清掉该集的人审回复残留(走通道公开接口,不绑定具体 provider 实现)
        self.review_channel.clear(f"{episode}:director")
        logger.info(f"{episode} director_review 已重置: {old} → pending")
        return True

    # ---------- context / task 构建 ----------

    def _build_agent_context(self, action: Action) -> dict:
        state = action.state
        ctx = {
            "project": self.project,
            "episode": action.episode,
            "episode_num": state.get("episode_num"),
            "act": state.get("act", ""),
            "state": state,
        }
        if action.shot:
            ctx["shot"] = action.shot
        if action.extra:
            ctx["extra"] = action.extra
        return ctx

    def _build_executor_task(self, action: Action) -> dict:
        root = self.project.project_root
        ep, state, shot, name = action.episode, action.state, action.shot, action.name
        shots_dir = self.project.get_path("art") / "shots"

        if name == "text2img":
            out = shots_dir / f"{shot['id']}.png"
            return {
                "shot_id": shot["id"],
                "prompt": shot["text2img"].get("prompt", ""),
                "negative_prompt": shot["text2img"].get("negative_prompt", ""),
                "scene": shot.get("scene", ""),
                "reference_images": [],
                "output_path": str(out),
            }
        if name == "img2video":
            img = root / shot["text2img"]["file"]
            out = shots_dir / f"{shot['id']}.mp4"
            return {
                "shot_id": shot["id"],
                "image_path": str(img),
                "prompt": shot["img2video"].get("prompt", ""),
                "output_path": str(out),
                "duration": int(shot.get("duration", 4)),
            }
        if name == "audio":
            out = self.project.get_path("audio") / f"{ep}.wav"
            return {"lines": self._collect_lines(state), "output_path": str(out)}
        if name == "compose":
            clips = [
                str(root / s["img2video"]["file"])
                for s in state.get("shots", [])
                if s["img2video"]["status"] == "approved" and s["img2video"].get("file")
            ]
            audio_rel = state["audio"].get("file")
            out = self.project.get_path("output") / f"{ep}.mp4"
            return {
                "video_clips": clips,
                "audio_path": str(root / audio_rel) if audio_rel else None,
                "output_path": str(out),
                "episode": ep,
            }
        return {}

    def _collect_lines(self, state: dict) -> list[dict]:
        lines = []
        for s in state.get("shots", []):
            d = (s.get("dialogue") or "").strip()
            if d:
                lines.append({"speaker": s.get("speaker", "旁白"), "text": d})
        if not lines:
            lines = [{"speaker": "旁白", "text": "本集无台词。"}]
        return lines

    # ---------- 结果应用 ----------

    def _rel(self, p: str) -> str:
        try:
            return str(Path(p).relative_to(self.project.project_root))
        except ValueError:
            return str(p)

    def _llm_cny(self, tokens: int) -> float:
        """token → ¥（按 config 的每千 token 单价）"""
        return round(tokens / 1000 * self.config.llm.price_per_1k_tokens, 6)

    def _check_cost_deviation(self) -> None:
        """运行结束：实际各环节占比 vs 基线，偏离超阈值则预警"""
        mon = self.config.raw.get("cost_monitor", {})
        if not mon.get("enabled"):
            return
        alerts = self.cost.check_deviation(
            mon.get("baseline_cost_share", {}),
            mon.get("baseline_token_share", {}),
            float(mon.get("deviation_threshold", 0.5)),
        )
        for a in alerts:
            logger.warning(f"消耗偏离: {a}")
            self.notifier.send(f"⚠️ 消耗偏离预警: {a}")
        if not alerts:
            logger.info("消耗结构在基线范围内，无偏离")

    def _apply_agent_result(self, action: Action, result: dict) -> None:
        ep, name = action.episode, action.name
        tokens = result.get("cost_tokens", 0)
        cny = self._llm_cny(tokens)
        self.cost.record_llm(name, tokens, cny)
        self.state_mgr.add_cost(ep, llm_tokens=tokens, cost_cny=cny)

        if name == "writer":
            self.state_mgr.update_task(ep, "script", status="approved",
                                       file=result.get("file"),
                                       attempts=action.state["script"]["attempts"] + 1)
        elif name == "storyboard":
            self.state_mgr.update_task(ep, "storyboard", status="approved",
                                       file=result.get("file"),
                                       shot_count=result.get("shot_count", 0),
                                       attempts=action.state["storyboard"]["attempts"] + 1)
            for spec in result.get("shots", []):
                self.state_mgr.add_shot(ep, new_shot_state(
                    spec["id"], spec.get("scene", ""), spec.get("type", "static"),
                    spec.get("t2i_prompt", ""), spec.get("i2v_prompt", ""),
                    spec.get("negative_prompt", ""), spec.get("dialogue", ""),
                    spec.get("speaker", "旁白"), int(spec.get("duration", 4)),
                ))
        elif name == "director":
            if action.extra and action.extra.get("reason"):
                self._apply_escalation(ep, action.shot, action.extra["reason"], result)
            elif result.get("approved"):
                # 注:auto 路径 approve 不写 notes(人审 approve 才写回复原文)。
                # 这是当前唯一 notes=None 的 approve 路径,可作"auto vs 人审"的区分指纹;
                # 若日后要给 auto 也记 notes,需另设显式来源字段,勿让此约定悄悄失效。
                self.state_mgr.update_task(ep, "director_review",
                                           status="approved", result="approved")
                self.notifier.send(f"🎬 {ep} 终审通过！")
            else:
                self.state_mgr.update_task(ep, "director_review",
                                           status="rejected",
                                           notes=result.get("overall_notes", ""))

    def _apply_escalation(self, ep, shot, reason, result) -> None:
        sub = "text2img" if reason.startswith("text2img") else "img2video"
        resolution = result.get("escalation_resolution", "downgrade")
        self.state_mgr.update_shot(ep, shot["id"], sub, status="escalated",
                                   qa_notes=result.get("action", resolution))
        if resolution == "manual":
            self.notifier.send(f"🛑 {ep} {shot['id']} {sub} 需人工干预（已标记 escalated）")
        else:
            self.notifier.send(f"⚠️ {ep} {shot['id']} {sub} 超重试预算 → {resolution}")

    def _apply_executor_result(self, action: Action, result: dict) -> None:
        ep, name, shot = action.episode, action.name, action.shot
        success = result.get("success", False)

        if name in ("text2img", "img2video"):
            sub = name
            attempts = shot[sub]["attempts"] + 1
            if not success:
                self.state_mgr.update_shot(ep, shot["id"], sub,
                                           status="qa_fail", attempts=attempts)
                return
            file_abs = result.get("file")
            cost = result.get("cost", 0.0)
            self.cost.record_api(sub, cost)
            self.state_mgr.add_cost(ep, api_calls=1, cost_cny=cost)
            passed = self._run_visual_qa(ep, shot, sub, file_abs)
            self.state_mgr.update_shot(
                ep, shot["id"], sub,
                status="approved" if passed else "qa_fail",
                file=self._rel(file_abs), attempts=attempts, cost=cost,
            )
        elif name == "audio":
            if success:
                cost = result.get("cost", 0.0)
                self.cost.record_api("audio", cost)
                self.state_mgr.add_cost(ep, api_calls=1, cost_cny=cost)
                self.state_mgr.update_task(ep, "audio", status="approved",
                                           file=self._rel(result.get("file")))
        elif name == "compose":
            if success:
                cost = result.get("cost", 0.0)
                self.cost.record_api("compose", cost)
                self.state_mgr.add_cost(ep, api_calls=1, cost_cny=cost)
                self.state_mgr.update_task(ep, "composite", status="approved",
                                           file=self._rel(result.get("file")))

    def _run_visual_qa(self, ep, shot, sub, file_abs) -> bool:
        """内联画面质检。离线/异常默认通过（避免卡死）。"""
        if "visual_qa" not in self.agents or not file_abs:
            return True
        ctx = {
            "project": self.project, "episode": ep, "shot": shot,
            "sub_task": sub, "file_path": str(file_abs),
        }
        try:
            res = self.agents["visual_qa"].run(ctx)
            tokens = res.get("cost_tokens", 0)
            cny = self._llm_cny(tokens)
            self.cost.record_llm("visual_qa", tokens, cny)
            self.state_mgr.add_cost(ep, llm_tokens=tokens, cost_cny=cny)
            return bool(res.get("pass", True))
        except Exception as e:
            logger.warning(f"visual_qa 异常，默认通过: {e}")
            return True

    # ---------- 状态报告 ----------

    def status(self) -> str:
        states = self.state_mgr.load_all()
        if not states:
            return "无状态文件。请先 --init 或 --init-episode 初始化。"
        lines = [f"项目: {self.project.name} | 集数: {len(states)}"]
        for s in states:
            shots = s.get("shots", [])
            done = sum(1 for sh in shots if _shot_done(sh))
            lines.append(
                f"  {s['episode']} | 剧本:{s['script']['status']} "
                f"分镜:{s['storyboard']['status']} 镜头:{done}/{len(shots)} "
                f"音频:{s['audio']['status']} 合成:{s['composite']['status']} "
                f"终审:{s['director_review']['status']}"
            )
        return "\n".join(lines)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="AI 短剧工厂 — 调度器")
    parser.add_argument("--project", required=True, help="项目目录路径 (如 projects/三官)")
    parser.add_argument("--config", default="config.yaml", help="全局配置文件路径")
    parser.add_argument("--episode", help="只处理指定集 (如 ep01)")
    parser.add_argument("--stage", help="只处理指定环节")
    parser.add_argument("--init", action="store_true", help="初始化所有集状态文件")
    parser.add_argument("--init-episode", help="只初始化指定集 (如 ep01)")
    parser.add_argument("--status", action="store_true", help="显示状态报告")
    parser.add_argument("--review-reply", nargs=3, metavar=("EPISODE", "STAGE", "REPLY"),
                        help="提交人审回复 (如 --review-reply ep01 director \"通过\")")
    parser.add_argument("--reset-review", metavar="EPISODE",
                        help="把被打回/审核中的集重置为 pending 重新激活 (如 --reset-review ep01)")
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    config = Config.from_yaml(args.config)
    project = ProjectConfig.from_yaml(Path(args.project) / "project.yaml")
    orch = Orchestrator(config, project)

    if args.init or args.init_episode:
        orch.init_states(args.init_episode)
        return
    if args.status:
        print(orch.status())
        return
    if args.review_reply:
        ep, stage, reply = args.review_reply
        orch.review_channel.submit(f"{ep}:{stage}", reply)
        print(f"已提交人审回复 [{ep}:{stage}]: {reply}")
        return
    if args.reset_review:
        ok = orch.reset_review(args.reset_review)
        print(f"{'已重置' if ok else '重置失败'}: {args.reset_review}")
        return
    orch.run(episode_filter=args.episode, stage_filter=args.stage)


if __name__ == "__main__":
    main()
