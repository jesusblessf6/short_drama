"""方舟 Agent Plan 视觉模型 provider 回归 — 图片(同步) + 视频(异步任务/恢复)

依据官方《接入视觉模型》PDF（references/火山方舟_接入视觉模型_*.pdf）：
- 专属 base_url https://ark.cn-beijing.volces.com/api/plan/v3（必须带 /plan）
- 图：POST /images/generations（model/prompt/size/watermark；data[0].url 或 b64_json）
- 视频：POST /contents/generations/tasks（content=[text, image_url(首帧)] + ratio/duration）
        → GET /tasks/{id} 轮询 → succeeded 后下载 video_url
全部 mock 传输层，零网络、零 AFP 消耗。
"""

import base64
import subprocess

import httpx
import pytest

from drama.executors.img2video import Img2VideoExecutor
from drama.executors.text2img import Text2ImgExecutor

from conftest import make_orchestrator


def _ark_api(orch, sub, model, **over):
    api = orch.config.apis[sub]
    api.provider = "ark"
    api.api_key = "test-key"
    api.model = model
    api.cost_per_call = 0.2
    for k, v in over.items():
        setattr(api, k, v)
    return api


def _png(tmp_path, name="img.png"):
    from PIL import Image
    p = tmp_path / name
    Image.new("RGB", (64, 64), (8, 8, 8)).save(p)
    return p


def _mp4(tmp_path, dur=2):
    out = tmp_path / f"v{dur}.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64",
                    "-r", "25", "-t", str(dur), str(out)],
                   check=True, capture_output=True)
    return out


def _json_resp(payload, status=200):
    return httpx.Response(status, json=payload,
                          request=httpx.Request("POST", "http://t"))


class TestArkText2Img:
    def test_url_response_downloads_and_payload_shape(self, tmp_path, monkeypatch):
        """payload 按官方文档（plan 端点/size/watermark/负面并入 prompt/参考图 base64）"""
        orch, _ = make_orchestrator(tmp_path)
        _ark_api(orch, "text2img", "doubao-seedream-5-0-pro")
        png = _png(tmp_path)
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured.update(url=url, json=json, headers=headers)
            return _json_resp({"data": [{"url": "https://tos/x.png"}]})

        monkeypatch.setattr(httpx, "post", fake_post)
        monkeypatch.setattr(httpx, "get", lambda url, **k: httpx.Response(
            200, content=png.read_bytes(), request=httpx.Request("GET", url)))

        out = tmp_path / "shot.png"
        result = Text2ImgExecutor(orch.config).run({
            "shot_id": "s1", "prompt": "中景，测试", "negative_prompt": "模糊",
            "scene": "s", "reference_images": [str(png)], "output_path": str(out)})

        assert result["success"] and result["source"] == "ark"
        assert captured["url"] == ("https://ark.cn-beijing.volces.com/api/plan/v3"
                                   "/images/generations")
        assert captured["headers"]["Authorization"] == "Bearer test-key"
        body = captured["json"]
        assert body["model"] == "doubao-seedream-5-0-pro"
        assert body["size"] == "1080x1920" and body["watermark"] is False
        assert body["output_format"] == "png"
        assert "避免出现" in body["prompt"] and "模糊" in body["prompt"]
        assert body["image"].startswith("data:image/png;base64,")
        assert out.exists()                       # 下载字节已过 PIL 验证落盘

    def test_b64_response_no_download(self, tmp_path, monkeypatch):
        orch, _ = make_orchestrator(tmp_path)
        _ark_api(orch, "text2img", "doubao-seedream-5-0-pro")
        png = _png(tmp_path)
        monkeypatch.setattr(
            httpx, "post",
            lambda url, **k: _json_resp(
                {"data": [{"b64_json": base64.b64encode(png.read_bytes()).decode()}]}))
        got = []
        monkeypatch.setattr(httpx, "get",
                            lambda url, **k: got.append(url))
        result = Text2ImgExecutor(orch.config).run({
            "shot_id": "s1", "prompt": "p", "output_path": str(tmp_path / "o.png")})
        assert result["success"] and not got          # b64 直用，无需下载

    def test_auth_error_is_fatal_class(self, tmp_path, monkeypatch):
        """401 → error_class=auth（M1 分流：鉴权失败直接终态，不空烧重试）"""
        orch, _ = make_orchestrator(tmp_path)
        _ark_api(orch, "text2img", "doubao-seedream-5-0-pro")
        monkeypatch.setattr(httpx, "post",
                            lambda url, **k: _json_resp({}, status=401))
        result = Text2ImgExecutor(orch.config).run({
            "shot_id": "s1", "prompt": "p", "output_path": str(tmp_path / "o.png")})
        assert result["success"] is False and result["error_class"] == "auth"


class TestArkImg2Video:
    def _wire(self, monkeypatch, statuses, mp4=None, submit_body=None):
        """接线 mock：POST 提交 → GET 轮询（statuses 序列耗尽后重复最后一个）→ GET 下载"""
        posts, gets = [], []
        submit = submit_body or {"id": "cgt-123"}
        state = {"i": 0}

        def fake_post(url, json=None, headers=None, timeout=None):
            posts.append((url, json))
            return _json_resp(submit)

        def fake_get(url, timeout=None, follow_redirects=None, headers=None):
            gets.append(url)
            if "/tasks/" in url:
                i = min(state["i"], len(statuses) - 1)
                state["i"] += 1
                return _json_resp({"status": statuses[i],
                                   "content": {"video_url": "https://tos/v.mp4"}})
            return httpx.Response(200, content=mp4.read_bytes(),
                                  request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "post", fake_post)
        monkeypatch.setattr(httpx, "get", fake_get)
        return posts, gets

    def test_full_flow_submit_poll_download(self, tmp_path, monkeypatch):
        orch, _ = make_orchestrator(tmp_path)
        _ark_api(orch, "img2video", "doubao-seedance-2.0", cost_per_call=1.0)
        still, mp4 = _png(tmp_path), _mp4(tmp_path)
        monkeypatch.setattr(Img2VideoExecutor, "POLL_INTERVAL", 0.0)
        posts, gets = self._wire(monkeypatch, ["running", "succeeded"], mp4=mp4)

        out = tmp_path / "shot.mp4"
        result = Img2VideoExecutor(orch.config).run({
            "shot_id": "s1", "image_path": str(still), "prompt": "缓推镜头",
            "output_path": str(out), "duration": 4, "external_task_id": None})

        assert result["success"] and result["source"] == "ark"
        assert result["cost"] == 1.0 and out.exists()
        url, body = posts[0]
        assert url.endswith("/contents/generations/tasks")
        assert body["model"] == "doubao-seedance-2.0"
        assert body["ratio"] == "9:16" and body["duration"] == 4
        assert body["watermark"] is False and body["generate_audio"] is False
        c = body["content"]
        assert c[0]["type"] == "text" and c[0]["text"] == "缓推镜头"
        assert c[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert any("/tasks/cgt-123" in g for g in gets)     # 轮询了任务

    def test_timeout_returns_submitted_with_task_id(self, tmp_path, monkeypatch):
        """轮询超时 → submitted+external_task_id（M1 契约：不算失败、下轮续轮询）"""
        orch, _ = make_orchestrator(tmp_path)
        _ark_api(orch, "img2video", "doubao-seedance-2.0")
        still = _png(tmp_path)
        monkeypatch.setattr(Img2VideoExecutor, "POLL_INTERVAL", 0.0)
        monkeypatch.setattr(Img2VideoExecutor, "POLL_TIMEOUT", 0.3)
        self._wire(monkeypatch, ["running"])

        result = Img2VideoExecutor(orch.config).run({
            "shot_id": "s1", "image_path": str(still), "prompt": "p",
            "output_path": str(tmp_path / "o.mp4"), "duration": 4,
            "external_task_id": None})
        assert result["success"] is False and result["submitted"] is True
        assert result["external_task_id"] == "cgt-123"

    def test_resume_skips_submit(self, tmp_path, monkeypatch):
        """恢复路径：task 带 external_task_id → 只轮询不重新提交（不重复扣费）"""
        orch, _ = make_orchestrator(tmp_path)
        _ark_api(orch, "img2video", "doubao-seedance-2.0")
        still, mp4 = _png(tmp_path), _mp4(tmp_path)
        monkeypatch.setattr(Img2VideoExecutor, "POLL_INTERVAL", 0.0)
        posts, _ = self._wire(monkeypatch, ["succeeded"], mp4=mp4)

        result = Img2VideoExecutor(orch.config).run({
            "shot_id": "s1", "image_path": str(still), "prompt": "p",
            "output_path": str(tmp_path / "o.mp4"), "duration": 4,
            "external_task_id": "cgt-123"})
        assert result["success"] and posts == []        # 零次提交

    def test_task_failure_is_retryable(self, tmp_path, monkeypatch):
        orch, _ = make_orchestrator(tmp_path)
        _ark_api(orch, "img2video", "doubao-seedance-2.0")
        still = _png(tmp_path)
        monkeypatch.setattr(Img2VideoExecutor, "POLL_INTERVAL", 0.0)
        self._wire(monkeypatch, ["failed"])
        result = Img2VideoExecutor(orch.config).run({
            "shot_id": "s1", "image_path": str(still), "prompt": "p",
            "output_path": str(tmp_path / "o.mp4"), "duration": 4,
            "external_task_id": None})
        assert result["success"] is False and "failed" in result["error"]
        assert result.get("error_class", "unknown") not in ("auth", "param")

    @pytest.mark.parametrize("model,duration,expected", [
        ("doubao-seedance-2.0", 30, 15),      # 2.0 上限 15s → 钳制
        ("doubao-seedance-2.5", 30, 30),      # 2.5 支持 4-30s
        ("doubao-seedance-2.0", 2, 4),        # 下限 4s
    ])
    def test_duration_clamp_by_model(self, model, duration, expected):
        assert Img2VideoExecutor._duration_range(model)[1] >= expected


class TestMissingKeyGuard:
    def test_real_provider_without_key_fails_fast(self, tmp_path):
        """provider=ark 但 key 为空 → 启动即明确报错（不空跑五轮 401 重试）"""
        orch, _ = make_orchestrator(tmp_path)
        for sub in ("text2img", "img2video"):
            orch.config.apis[sub].provider = "ark"
            orch.config.apis[sub].api_key = ""
        with pytest.raises(SystemExit, match="AGENT_API_KEY"):
            orch.run(episode_filter="ep01")

    def test_placeholder_without_key_still_runs(self, tmp_path):
        """离线承诺不破：placeholder 无 key 照常（不触发拦截）"""
        orch, _ = make_orchestrator(tmp_path)
        assert all(orch.config.apis[s].provider == "placeholder"
                   for s in ("text2img", "img2video"))
        try:
            orch.run(episode_filter="ep01")   # 能进入主流程（跑完占位管线）
        except SystemExit as e:
            pytest.fail(f"placeholder 模式不应被 key 拦截: {e}")
