"""MiniMax H3 视频生成 provider 回归 — seedance 备用线路（v2 API）

wire 协议依据 platform.minimax.cn 文档：
- 创建 POST {base}/v2/video_generation → {"task_id"}；content 首帧必须 role:"first_frame"，
  resolution 必填（480P/768P/2K），duration 4-15s，aigc_watermark
- 查询 GET {base}/v2/query/video_generation/{task_id} → {"task": {status, content.url}}
- status: queued/running/succeeded/failed/cancelled；402 余额不足、422 敏感内容 → 不可重试
全部 mock 传输层，零消耗。
"""

import base64
import subprocess

import httpx
import pytest

from drama.executors.img2video import Img2VideoExecutor

from conftest import make_orchestrator


def _minimax_api(orch, **over):
    api = orch.config.apis["img2video"]
    api.provider = "minimax"
    api.api_key = "mm-key"
    api.model = "MiniMax-H3"
    api.cost_per_call = 1.0
    api.base_url = "https://api.minimax.cn"
    api.extra = {"resolution": "768P", "ratio": "adaptive"}
    for k, v in over.items():
        setattr(api, k, v)
    return api


def _png(tmp_path):
    from PIL import Image
    p = tmp_path / "img.png"
    Image.new("RGB", (64, 64), (8, 8, 8)).save(p)
    return p


def _mp4(tmp_path, dur=2):
    out = tmp_path / f"v{dur}.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64",
                    "-r", "25", "-t", str(dur), str(out)],
                   check=True, capture_output=True)
    return out


def _json(payload, status=200):
    return httpx.Response(status, json=payload,
                          request=httpx.Request("POST", "http://t"))


class TestMinimaxFlow:
    def _wire(self, monkeypatch, statuses, mp4):
        posts, gets = [], []
        state = {"i": 0}

        def fake_post(url, json=None, headers=None, timeout=None):
            posts.append((url, json, headers))
            return _json({"task_id": "424010985738629"})

        def fake_get(url, timeout=None, follow_redirects=None, headers=None):
            gets.append(url)
            if "/v2/query/video_generation/" in url:
                i = min(state["i"], len(statuses) - 1)
                state["i"] += 1
                task = {"status": statuses[i]}
                if statuses[i] == "succeeded":
                    task["content"] = {"url": "https://cdn/out.mp4"}
                if statuses[i] == "failed":
                    task["error"] = {"code": "gen_error", "message": "内部错误"}
                return _json({"task": task})
            return httpx.Response(200, content=mp4.read_bytes(),
                                  request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "post", fake_post)
        monkeypatch.setattr(httpx, "get", fake_get)
        return posts, gets

    def test_full_flow_payload_and_download(self, tmp_path, monkeypatch):
        """payload 契约：v2 端点 / role:first_frame / resolution 必填 / duration 钳制 / 无水印"""
        orch, _ = make_orchestrator(tmp_path)
        _minimax_api(orch)
        still, mp4 = _png(tmp_path), _mp4(tmp_path)
        monkeypatch.setattr(Img2VideoExecutor, "POLL_INTERVAL", 0.0)
        posts, gets = self._wire(monkeypatch, ["queued", "running", "succeeded"], mp4)

        out = tmp_path / "shot.mp4"
        result = Img2VideoExecutor(orch.config).run({
            "shot_id": "s1", "image_path": str(still), "prompt": "缓推镜头",
            "output_path": str(out), "duration": 20, "external_task_id": None})

        assert result["success"] and result["source"] == "minimax"
        assert out.exists()
        url, body, headers = posts[0]
        assert url == "https://api.minimax.cn/v2/video_generation"
        assert headers["Authorization"] == "Bearer mm-key"
        assert body["model"] == "MiniMax-H3"
        assert body["resolution"] == "768P"
        assert body["duration"] == 15          # 20 → 钳制到上限
        assert body["aigc_watermark"] is False
        c = body["content"]
        assert c[0]["type"] == "text" and c[0]["text"] == "缓推镜头"
        assert c[1]["role"] == "first_frame"
        assert c[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert any("/v2/query/video_generation/424010985738629" in g for g in gets)

    def test_timeout_returns_submitted(self, tmp_path, monkeypatch):
        orch, _ = make_orchestrator(tmp_path)
        _minimax_api(orch)
        monkeypatch.setattr(Img2VideoExecutor, "POLL_INTERVAL", 0.0)
        monkeypatch.setattr(Img2VideoExecutor, "POLL_TIMEOUT", 0.3)
        self._wire(monkeypatch, ["running"], _mp4(tmp_path))
        result = Img2VideoExecutor(orch.config).run({
            "shot_id": "s1", "image_path": str(_png(tmp_path)), "prompt": "p",
            "output_path": str(tmp_path / "o.mp4"), "duration": 5,
            "external_task_id": None})
        assert result["success"] is False and result["submitted"] is True
        assert result["external_task_id"] == "424010985738629"

    def test_resume_skips_submit(self, tmp_path, monkeypatch):
        orch, _ = make_orchestrator(tmp_path)
        _minimax_api(orch)
        monkeypatch.setattr(Img2VideoExecutor, "POLL_INTERVAL", 0.0)
        posts, _ = self._wire(monkeypatch, ["succeeded"], _mp4(tmp_path))
        result = Img2VideoExecutor(orch.config).run({
            "shot_id": "s1", "image_path": str(_png(tmp_path)), "prompt": "p",
            "output_path": str(tmp_path / "o.mp4"), "duration": 5,
            "external_task_id": "424010985738629"})
        assert result["success"] and posts == []

    def test_task_failure_is_retryable(self, tmp_path, monkeypatch):
        orch, _ = make_orchestrator(tmp_path)
        _minimax_api(orch)
        monkeypatch.setattr(Img2VideoExecutor, "POLL_INTERVAL", 0.0)
        self._wire(monkeypatch, ["failed"], _mp4(tmp_path))
        result = Img2VideoExecutor(orch.config).run({
            "shot_id": "s1", "image_path": str(_png(tmp_path)), "prompt": "p",
            "output_path": str(tmp_path / "o.mp4"), "duration": 5,
            "external_task_id": None})
        assert result["success"] is False
        assert "failed" in result["error"]
        assert result["error_class"] not in ("auth", "param")

    def test_insufficient_balance_maps_to_param_terminal(self, tmp_path, monkeypatch):
        """402 余额不足：classify_exception 归 param（重试无意义 → M1 终态分流）"""
        from drama.utils.retry import classify_exception
        orch, _ = make_orchestrator(tmp_path)
        _minimax_api(orch)
        monkeypatch.setattr(
            httpx, "post",
            lambda url, **k: _json({"error": {"type": "insufficient_balance"}},
                                   status=402))
        result = Img2VideoExecutor(orch.config).run({
            "shot_id": "s1", "image_path": str(_png(tmp_path)), "prompt": "p",
            "output_path": str(tmp_path / "o.mp4"), "duration": 5,
            "external_task_id": None})
        assert result["success"] is False
        assert result["error_class"] == classify_exception(
            httpx.HTTPStatusError("x", request=httpx.Request("POST", "u"),
                                  response=httpx.Response(402)))

    def test_default_base_url_when_config_holds_ark(self, tmp_path, monkeypatch):
        """config.base_url 还是 ark 默认值时，minimax 端点兜底正确（防跨家混用）"""
        orch, _ = make_orchestrator(tmp_path)
        _minimax_api(orch, base_url="https://ark.cn-beijing.volces.com/api/plan/v3")
        monkeypatch.setattr(Img2VideoExecutor, "POLL_INTERVAL", 0.0)
        posts, _ = self._wire(monkeypatch, ["succeeded"], _mp4(tmp_path))
        Img2VideoExecutor(orch.config).run({
            "shot_id": "s1", "image_path": str(_png(tmp_path)), "prompt": "p",
            "output_path": str(tmp_path / "o.mp4"), "duration": 5,
            "external_task_id": None})
        assert posts[0][0] == "https://api.minimax.cn/v2/video_generation"
