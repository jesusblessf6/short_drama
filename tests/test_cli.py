"""CLI 冒烟测试 — argparse 接线与退出码（子进程跑真实入口）"""

import os
import subprocess
import sys

from conftest import REPO_ROOT, make_global_config, make_project


def _cli(*args, config_path, project_root):
    env = os.environ.copy()
    env.pop("ARK_CODING_API_KEY", None)   # 确保离线，不依赖 shell 环境
    return subprocess.run(
        [sys.executable, "-m", "drama.orchestrator",
         "--project", str(project_root), "--config", str(config_path), *args],
        capture_output=True, text=True, cwd=REPO_ROOT, env=env, timeout=120,
    )


def test_cli_init_and_status(tmp_path):
    root = make_project(tmp_path)
    cfg = make_global_config(tmp_path)

    r = _cli("--init-episode", "ep01", config_path=cfg, project_root=root)
    assert r.returncode == 0, r.stderr
    assert (root / ".state" / "ep01.yaml").exists()

    r = _cli("--status", config_path=cfg, project_root=root)
    assert r.returncode == 0, r.stderr
    assert "ep01" in r.stdout
    assert "pending" in r.stdout


def test_cli_review_reply_submits(tmp_path):
    root = make_project(tmp_path)
    cfg = make_global_config(tmp_path)

    _cli("--init-episode", "ep01", config_path=cfg, project_root=root)
    r = _cli("--review-reply", "ep01", "director", "通过",
             config_path=cfg, project_root=root)
    assert r.returncode == 0, r.stderr
    reply = root / ".state" / "reviews" / "ep01__director.reply.txt"
    assert reply.exists() and reply.read_text(encoding="utf-8") == "通过"


def test_cli_reset_review_missing_episode(tmp_path):
    """不存在的集 reset 应失败但进程不崩（退出码 0、打印失败信息）"""
    root = make_project(tmp_path)
    cfg = make_global_config(tmp_path)
    r = _cli("--reset-review", "ep404", config_path=cfg, project_root=root)
    assert r.returncode == 0
    assert "失败" in r.stdout
