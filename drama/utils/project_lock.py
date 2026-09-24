"""单项目运行锁

防止 CLI 与机器人/另一 CLI 同时操作同一项目（M1：DEVELOPMENT_PLAN
"单项目运行锁"）。用 fcntl.flock 排他锁；拿不到立即失败，不等待——
双写同一 YAML 的后果是状态互相覆盖，宁可直接退出。

锁文件是 .state/ 下的空文件，进程崩溃后内核自动释放（flock 特性），
无需清理残留。
"""

import fcntl
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ProjectLock:
    """用法:
        lock = ProjectLock(state_dir / ".lock")
        lock.acquire()   # 拿不到抛 RuntimeError
        ...
        lock.release()
    或作为上下文管理器使用。同一进程内重复 acquire 也会失败（不同 fd）。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fd = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = open(self.path, "w")
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._fd.close()
            self._fd = None
            raise RuntimeError(
                f"项目已在运行中（锁被占用: {self.path}）。"
                f"请确认没有另一个调度器/机器人在操作同一项目后重试。")

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
            self._fd = None

    def __enter__(self) -> "ProjectLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
