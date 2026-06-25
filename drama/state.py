"""状态管理模块

每集一个 YAML 状态文件，记录该集所有环节的当前状态。
Orchestrator 读写状态文件来决定下一步做什么。
"""

from pathlib import Path
from datetime import datetime
from typing import Any

import yaml


# 状态枚举
TASK_STATUSES = {"pending", "drafting", "reviewing", "approved", "rejected"}
SHOT_STATUSES = {"pending", "generating", "qa_pass", "qa_fail", "approved", "escalated"}


def new_episode_state(episode_num: int, act: str) -> dict:
    """创建新集的初始状态"""
    ep_id = f"ep{episode_num:02d}"
    return {
        "episode": ep_id,
        "episode_num": episode_num,
        "act": act,
        "updated_at": datetime.now().isoformat(),
        "script": {
            "status": "pending",
            "file": None,
            "attempts": 0,
            "cost_tokens": 0,
        },
        "storyboard": {
            "status": "pending",
            "file": None,
            "attempts": 0,
            "cost_tokens": 0,
            "shot_count": 0,
        },
        "shots": [],
        "audio": {
            "status": "pending",
            "file": None,
        },
        "composite": {
            "status": "pending",
            "file": None,
        },
        "director_review": {
            "status": "pending",
            "result": None,
            "notes": None,
        },
        "cost_summary": {
            "llm_tokens": 0,
            "api_calls": 0,
            "cost_cny": 0.0,
        },
    }


def new_shot_state(
    shot_id: str,
    scene: str = "",
    shot_type: str = "static",
    t2i_prompt: str = "",
    i2v_prompt: str = "",
    negative_prompt: str = "",
    dialogue: str = "",
    speaker: str = "旁白",
    duration: int = 4,
) -> dict:
    """创建新镜头的初始状态。

    shot_type ∈ {static, simple, complex, lip_sync}，决定重试预算。
    t2i_prompt / i2v_prompt / negative_prompt 由 storyboard 产出，供 executor 构建 task。
    dialogue 该镜头台词、speaker 说话人（供 audio 按角色/旁白切声），duration 镜头时长（秒）。
    """
    return {
        "id": shot_id,
        "scene": scene,
        "type": shot_type,
        "dialogue": dialogue,
        "speaker": speaker,
        "duration": duration,
        "text2img": {
            "status": "pending",
            "file": None,
            "prompt": t2i_prompt,
            "negative_prompt": negative_prompt,
            "prompt_file": None,
            "attempts": 0,
            "cost": 0.0,
        },
        "img2video": {
            "status": "pending",
            "file": None,
            "prompt": i2v_prompt,
            "prompt_file": None,
            "attempts": 0,
            "cost": 0.0,
            "qa_notes": None,
            "last_attempt": None,
        },
    }


class StateManager:
    """状态文件读写管理器"""

    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _state_path(self, episode: str) -> Path:
        return self.state_dir / f"{episode}.yaml"

    def load(self, episode: str) -> dict:
        """加载某集的状态"""
        path = self._state_path(episode)
        if not path.exists():
            return None
        with open(path) as f:
            return yaml.safe_load(f)

    def save(self, episode: str, state: dict) -> None:
        """保存某集的状态"""
        state["updated_at"] = datetime.now().isoformat()
        path = self._state_path(episode)
        with open(path, "w") as f:
            yaml.dump(state, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def load_all(self) -> list[dict]:
        """加载所有集的状态"""
        states = []
        for path in sorted(self.state_dir.glob("ep*.yaml")):
            with open(path) as f:
                states.append(yaml.safe_load(f))
        return states

    def init_episode(self, episode_num: int, act: str) -> dict:
        """初始化一集的状态文件"""
        ep_id = f"ep{episode_num:02d}"
        state = new_episode_state(episode_num, act)
        self.save(ep_id, state)
        return state

    def update_task(self, episode: str, task_name: str, **updates) -> dict:
        """更新某集某个任务环节的状态字段"""
        state = self.load(episode)
        if state is None:
            raise ValueError(f"状态文件不存在: {episode}")
        if task_name not in state:
            raise ValueError(f"任务 '{task_name}' 不在状态中")
        state[task_name].update(updates)
        self.save(episode, state)
        return state

    def update_shot(self, episode: str, shot_id: str, sub_task: str, **updates) -> dict:
        """更新某集某镜头的子任务状态"""
        state = self.load(episode)
        if state is None:
            raise ValueError(f"状态文件不存在: {episode}")
        for shot in state["shots"]:
            if shot["id"] == shot_id:
                shot[sub_task].update(updates)
                self.save(episode, state)
                return state
        raise ValueError(f"镜头 '{shot_id}' 不在 {episode} 中")

    def add_cost(self, episode: str, llm_tokens: int = 0, api_calls: int = 0,
                 cost_cny: float = 0.0) -> None:
        """把单环节消耗累加进该集 cost_summary（per-episode 入账）"""
        state = self.load(episode)
        if state is None:
            return
        cs = state.setdefault("cost_summary",
                              {"llm_tokens": 0, "api_calls": 0, "cost_cny": 0.0})
        cs["llm_tokens"] = cs.get("llm_tokens", 0) + llm_tokens
        cs["api_calls"] = cs.get("api_calls", 0) + api_calls
        cs["cost_cny"] = round(cs.get("cost_cny", 0.0) + cost_cny, 4)
        self.save(episode, state)

    def add_shot(self, episode: str, shot: dict) -> None:
        """添加镜头到某集"""
        state = self.load(episode)
        if state is None:
            raise ValueError(f"状态文件不存在: {episode}")
        state["shots"].append(shot)
        self.save(episode, state)

    def find_pending_shot(self, state: dict) -> dict | None:
        """找到第一个未完成的镜头（img2video 未 approved）。

        只负责 surface，不做重试/升级判定（那是 Orchestrator._plan_shot_action 的唯一职责）。
        关键：耗尽重试的镜头也会被 surface（不再像旧版硬编码 <5 把它过滤掉导致整集卡死）。
        """
        for shot in state.get("shots", []):
            if shot["img2video"]["status"] != "approved":
                return shot
        return None

    def all_shots_approved(self, state: dict) -> bool:
        """检查某集所有镜头是否都已通过"""
        shots = state.get("shots", [])
        if not shots:
            return False
        return all(s["img2video"]["status"] == "approved" for s in shots)

    def reset_interrupted(self, state: dict) -> dict:
        """重置中断的任务（generating → pending）"""
        for shot in state.get("shots", []):
            if shot["text2img"]["status"] == "generating":
                shot["text2img"]["status"] = "pending"
            if shot["img2video"]["status"] == "generating":
                shot["img2video"]["status"] = "pending"
        for task in ["script", "storyboard"]:
            if state[task]["status"] == "drafting":
                state[task]["status"] = "pending"
        return state
