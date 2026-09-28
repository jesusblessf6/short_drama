"""M2 回归 — 结构化校验、媒体验证、抽帧质检、音色映射、字幕与音画对齐

对应 DEVELOPMENT_PLAN.md M2 中无需用户输入的离线部分：
- storyboard 结构化校验 + 机械修复 + 格式修复次数上限（真实路径）
- 内容红线 prompt（只做存在性检查，防回归删除）
- 产物文件有效性验证（ffprobe 缺失时 ffmpeg 兜底）
- 视频多时间点抽帧 → visual_qa 上下文
- 角色→音色映射、逐句时间戳 + SRT、compose 对齐修正（弃 -shortest）
"""

import subprocess

import yaml

from drama.agents.validation import (
    repair_shots, validate_shots,
)
from drama.executors.audio import AudioExecutor
from drama.executors.compose import ComposeExecutor
from drama.executors.img2video import Img2VideoExecutor
from drama.orchestrator import Action
from drama.state import new_episode_state, new_shot_state
from drama.utils.media_check import (
    extract_frames, frame_timestamps, validate_image, validate_video,
)
from drama.utils.subtitles import build_srt, format_srt_time

from conftest import make_orchestrator


# ---------- M2-2: 结构化校验与修复 ----------

class TestShotValidation:
    def test_repair_renumbers_duplicate_and_missing_ids(self):
        shots = [
            {"id": "", "type": "weird", "duration": 0, "t2i_prompt": "",
             "i2v_prompt": "", "scene": "A", "speaker": None, "dialogue": None},
            {"id": "ep01_shot01", "type": "simple", "duration": 99,
             "t2i_prompt": "x", "i2v_prompt": "y", "scene": "B"},
            {"id": "ep01_shot01", "type": "complex", "duration": 4,
             "t2i_prompt": "x", "i2v_prompt": "y", "scene": "C"},
        ]
        repair_shots(shots, "ep01")
        ids = [s["id"] for s in shots]
        assert len(set(ids)) == 3 and all(ids)          # 唯一且非空
        assert shots[0]["type"] == "static"              # 非法 type → static
        assert shots[1]["duration"] == 10                # 99 → 钳制到上限
        assert shots[0]["t2i_prompt"]                    # 空 prompt → 场景兜底
        assert shots[0]["speaker"] == "旁白"             # None → 旁白

    def test_validate_flags_and_clears(self):
        fatal, _ = validate_shots([])
        assert fatal
        shots = repair_shots([
            {"id": "a", "id2": 1, "type": "simple", "duration": 4,
             "t2i_prompt": "x", "i2v_prompt": "y", "scene": "S",
             "speaker": "路人甲", "dialogue": "hi"},
        ], "ep01")
        fatal, warns = validate_shots(shots, characters={"主角"})
        assert not fatal
        assert any("路人甲" in w for w in warns)          # 未知角色 → warning 不阻断

    def test_red_lines_present_in_prompts(self):
        """内容红线写进 writer/director prompt（M2-2 合规前置，防回归删除）"""
        from drama.agents.base import PROMPTS_DIR
        writer = (PROMPTS_DIR / "writer.md").read_text(encoding="utf-8")
        director = (PROMPTS_DIR / "director.md").read_text(encoding="utf-8")
        assert "内容红线" in writer and "血腥" in writer
        assert "内容红线" in director


class TestStoryboardRepairLoop:
    def _real_mode(self, orch):
        orch.config.llm.offline = False
        orch.config.llm.api_key = "k"
        return orch

    def _bad_markdown(self):
        return "### 镜头01\n- 场景：甲\n- 文生图Prompt：\n- 图生视频Prompt：\n"

    def _good_markdown(self):
        return ("### 镜头01\n- 场景：甲\n- 文生图Prompt：中景，甲，古风\n"
                "- 图生视频Prompt：中景固定镜头\n- 台词：旁白：夜色深沉\n")

    def test_offline_shots_pass_repair(self, tmp_path):
        """离线模板输出经 repair/validate 后保持合法（校验作为保险丝常开）"""
        orch, proj = make_orchestrator(tmp_path)
        agent = orch.agents["storyboard"]
        orch.init_states("ep01")
        state = orch.state_mgr.load("ep01")
        state["script"]["status"] = "approved"
        orch.state_mgr.save("ep01", state)
        ctx = {"project": proj, "state": orch.state_mgr.load("ep01")}
        result = agent.run(ctx)
        assert result["shot_count"] == 3
        assert len({s["id"] for s in result["shots"]}) == 3

    def test_real_path_repairs_then_accepts(self, tmp_path):
        """真实路径：首轮输出空 prompt（硬伤）→ 喂回错误重试 → 次轮合法被接受"""
        orch, proj = make_orchestrator(tmp_path)
        self._real_mode(orch)
        agent = orch.agents["storyboard"]
        orch.init_states("ep01")
        state = orch.state_mgr.load("ep01")
        state["script"]["status"] = "approved"
        orch.state_mgr.save("ep01", state)

        responses = iter([self._bad_markdown(), self._good_markdown()])
        calls = []
        agent.llm.chat = lambda messages: (calls.append(1), next(responses))[1]
        ctx = {"project": proj, "state": orch.state_mgr.load("ep01")}
        result = agent.run(ctx)
        assert len(calls) == 2                            # 首轮硬伤触发了一次修复
        assert result["shots"][0]["t2i_prompt"] == "中景，甲，古风"

    def test_real_path_cap_bounds_llm_then_mechanical_fallback(self, tmp_path):
        """修复次数上限：始终非法 → LLM 调用有界（cap+1），最终机械修复兜底放行"""
        orch, proj = make_orchestrator(tmp_path)
        self._real_mode(orch)
        orch.project.production["max_format_repairs"] = 1
        agent = orch.agents["storyboard"]
        calls = []
        agent.llm.chat = lambda messages: (calls.append(1), self._bad_markdown())[1]
        ctx = {"project": proj, "state": new_episode_state(1, "幕")}
        result = agent.run(ctx)
        assert len(calls) == 2                            # cap=1 → 首次+1次修复
        assert result["shots"][0]["t2i_prompt"]           # 机械兜底已填 prompt

    def test_real_path_raises_when_unrepairable(self, tmp_path):
        """机械修复也救不了（如零镜头）→ 拒绝进入生成"""
        orch, proj = make_orchestrator(tmp_path)
        self._real_mode(orch)
        agent = orch.agents["storyboard"]
        agent.llm.chat = lambda messages: "乱七八糟的输出"
        # 绕过 parse_output 的单镜头兜底，模拟连兜底都失效的极端情况
        agent.parse_output = lambda response, context: {
            "file": "x.md", "shot_count": 0, "shots": []}
        ctx = {"project": proj, "state": new_episode_state(1, "幕")}
        try:
            agent.run(ctx)
            assert False, "应抛 ValueError"
        except ValueError as e:
            assert "拒绝进入生成" in str(e)


# ---------- M2-4: 媒体验证与抽帧 ----------

class TestMediaCheck:
    def _placeholder_png(self, tmp_path):
        from PIL import Image
        p = tmp_path / "img.png"
        Image.new("RGB", (64, 64), (10, 10, 10)).save(p)
        return p

    def _placeholder_mp4(self, tmp_path, dur=2):
        out = tmp_path / f"v{dur}.mp4"
        # lavfi color 源默认无限长（勿加 d=，否则源 1s 截断、后续时间点抽不到帧）
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        "color=c=black:s=64x64", "-r", "25",
                        "-t", str(dur), str(out)], check=True, capture_output=True)
        return out

    def test_validate_image(self, tmp_path):
        ok, _ = validate_image(self._placeholder_png(tmp_path))
        assert ok
        junk = tmp_path / "junk.png"
        junk.write_bytes(b"not an image")
        ok, reason = validate_image(junk)
        assert not ok and "解码" in reason

    def test_validate_video_with_and_without_ffprobe(self, tmp_path):
        """ffprobe 可用与缺失（ffmpeg 兜底）两条路径都必须有效"""
        mp4 = self._placeholder_mp4(tmp_path)
        from drama.utils.media_check import resolve_ffprobe
        fp = resolve_ffprobe()
        ok, reason = validate_video(mp4, fp or "ffprobe-missing",
                                    ffmpeg="ffmpeg")
        assert ok, reason
        junk = tmp_path / "junk.mp4"
        junk.write_bytes(b"garbage")
        ok, _ = validate_video(junk, fp or "ffprobe-missing", ffmpeg="ffmpeg")
        assert not ok

    def test_extract_frames_multi_timestamps(self, tmp_path):
        mp4 = self._placeholder_mp4(tmp_path, dur=3)
        frames = extract_frames(mp4, tmp_path / "frames",
                                frame_timestamps(3.0))
        assert len(frames) == 3
        assert all(f.exists() and f.stat().st_size > 0 for f in frames)

    def test_orchestrator_passes_frames_to_qa(self, tmp_path):
        """_run_visual_qa 为视频构建 frames 上下文（10%/50%/90% 抽帧）"""
        orch, proj = make_orchestrator(tmp_path)
        captured = {}

        def stub_run(ctx):
            captured.update(ctx)
            return {"pass": True, "cost_tokens": 0}

        orch.agents["visual_qa"] = type("StubQA", (), {"run": staticmethod(stub_run)})()
        mp4 = self._placeholder_mp4(tmp_path, dur=2)
        shot = new_shot_state("ep01_shot01")
        assert orch._run_visual_qa("ep01", shot, "img2video", mp4) is True
        assert len(captured.get("frames", [])) == 3


# ---------- M2-5: 音色映射 / 字幕 / 对齐 ----------

class TestVoiceMapping:
    def test_voice_routing(self, tmp_path):
        orch, _ = make_orchestrator(tmp_path)
        ex = AudioExecutor(orch.config)
        cfg = orch.config.apis["audio"]
        cfg.voice = "default-voice"
        cfg.narrator_voice = "narrator-voice"
        vmap = {"商三官": "sanguan-voice"}
        assert ex._voice_for("旁白", cfg, vmap) == "narrator-voice"
        assert ex._voice_for("商三官", cfg, vmap) == "sanguan-voice"
        assert ex._voice_for("未知角色", cfg, vmap) == "default-voice"
        assert ex._voice_for("", cfg, vmap) == "narrator-voice"

    def test_project_voice_map_flows_into_task(self, tmp_path):
        orch, _ = make_orchestrator(tmp_path)
        orch.project.production["voice_map"] = {"商三官": "v1"}
        state = new_episode_state(1, "幕")
        task = orch._build_executor_task(
            Action("executor", "audio", "ep01", state))
        assert task["voice_map"] == {"商三官": "v1"}


class TestSubtitlesAndTimings:
    def test_format_srt_time(self):
        assert format_srt_time(0) == "00:00:00,000"
        assert format_srt_time(1.6) == "00:00:01,600"
        assert format_srt_time(3661.25) == "01:01:01,250"

    def test_build_srt_orders_and_skips_empty(self):
        srt = build_srt([
            {"start": 4.0, "duration": 2.0, "text": "第二句"},
            {"start": 0.0, "duration": 2.0, "text": "第一句"},
            {"start": 8.0, "duration": 2.0, "text": ""},
        ])
        assert srt.index("第一句") < srt.index("第二句")
        assert "第二句" in srt and srt.count("-->") == 2   # 空文本行跳过
        assert "00:00:04,000 --> 00:00:06,000" in srt

    def test_silent_fallback_writes_srt_and_manifest(self, tmp_path, offline_audio):
        """降级静音轨同样产出字幕与逐句清单（degraded 标记不变）"""
        orch, _ = make_orchestrator(tmp_path)
        ex = AudioExecutor(orch.config)
        out = tmp_path / "ep01.wav"
        result = ex.run({
            "lines": [{"speaker": "商三官", "text": "爹"},
                      {"speaker": "旁白", "text": "夜里"}],
            "output_path": str(out),
        })
        assert result["success"] and result["degraded"]
        srt = tmp_path / "ep01.srt"
        manifest = tmp_path / "ep01.lines.yaml"
        assert srt.exists() and manifest.exists()
        content = srt.read_text(encoding="utf-8")
        assert "爹" in content and "夜里" in content
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        assert data["degraded"] is True and len(data["lines"]) == 2
        assert data["lines"][1]["start"] >= data["lines"][0]["duration"]


class TestComposeAlignment:
    def _make_clips(self, tmp_path, n=2, dur=4):
        from PIL import Image
        img = tmp_path / "img.png"
        Image.new("RGB", (64, 64), (1, 2, 3)).save(img)
        clips = []
        for i in range(n):
            c = tmp_path / f"clip{i}.mp4"
            subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", str(img),
                            "-t", str(dur), "-r", "25",
                            "-vf", "scale=64:64,format=yuv420p",
                            "-c:v", "libx264", str(c)],
                           check=True, capture_output=True)
            clips.append(c)
        return clips

    def _make_audio(self, tmp_path, dur):
        a = tmp_path / f"a{dur}.wav"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi",
                        "-i", "anullsrc=r=44100:cl=stereo",
                        "-t", str(dur), str(a)], check=True, capture_output=True)
        return a

    def _probe(self, path):
        from drama.utils.media_check import probe_duration
        return probe_duration(path, ffmpeg="ffmpeg")

    def test_short_video_padded_to_audio(self, tmp_path):
        """视频 8s < 音频 10s：末帧补齐，音频台词不被 -shortest 截断"""
        orch, _ = make_orchestrator(tmp_path)
        ex = ComposeExecutor(orch.config)
        out = tmp_path / "out.mp4"
        result = ex.run({
            "video_clips": [str(c) for c in self._make_clips(tmp_path)],
            "audio_path": str(self._make_audio(tmp_path, 10)),
            "output_path": str(out), "episode": "ep01",
        })
        assert result["success"], result.get("error")
        assert result["alignment"]["strategy"] == "video_padded_to_audio"
        assert self._probe(out) >= 9.9          # 成片时长 ≈ 音频（≥视频 8s）

    def test_longer_video_keeps_full_length(self, tmp_path):
        """音频 3s < 视频 8s：不再截视频（旧 -shortest 会砍成 3s）"""
        orch, _ = make_orchestrator(tmp_path)
        ex = ComposeExecutor(orch.config)
        out = tmp_path / "out2.mp4"
        result = ex.run({
            "video_clips": [str(c) for c in self._make_clips(tmp_path)],
            "audio_path": str(self._make_audio(tmp_path, 3)),
            "output_path": str(out), "episode": "ep01",
        })
        assert result["success"]
        assert result["alignment"]["strategy"] == "as_is"
        assert self._probe(out) >= 7.9          # 保留完整视频时长

    def test_subtitles_burned_or_soft(self, tmp_path):
        """SRT 接入：烧录优先（需 ffmpeg 带 libass），失败回退软字幕轨；结果记录模式。

        无 libass 的精简 ffmpeg（如本机单二进制）两种封装都可能不可用——
        此时断言的是"字幕缺失不阻断合成"（降级韧性），而非具体模式。
        """
        import subprocess as sp
        has_subtitles_filter = sp.run(
            ["ffmpeg", "-filters"], capture_output=True, text=True
        ).stdout.find(" subtitles ") != -1
        orch, _ = make_orchestrator(tmp_path)
        ex = ComposeExecutor(orch.config)
        srt = tmp_path / "ep01.srt"
        srt.write_text(
            "1\n00:00:00,000 --> 00:00:02,000\n你好\n", encoding="utf-8")
        out = tmp_path / "out3.mp4"
        result = ex.run({
            "video_clips": [str(c) for c in self._make_clips(tmp_path)],
            "subtitles": str(srt),
            "output_path": str(out), "episode": "ep01",
        })
        assert result["success"], result.get("error")
        if has_subtitles_filter:
            assert result["subtitle_mode"] in ("burned", "soft")
        else:
            assert result["subtitle_mode"] in ("burned", "soft", "none")

    def test_e2e_audio_subtitle_file_reaches_state(self, tmp_path, offline_audio):
        """e2e：audio 的 subtitle_file 写入状态（供 compose 取用）"""
        orch, proj = make_orchestrator(tmp_path)
        orch.init_states("ep01")
        state = new_episode_state(1, "幕")
        state["audio"]["status"] = "pending"
        orch.state_mgr.save("ep01", state)
        action = Action("executor", "audio", "ep01", state)
        orch._apply_executor_result(action, {
            "success": True, "file": str(proj.get_path("audio") / "ep01.wav"),
            "cost": 0.0, "source": "edge_tts", "degraded": False,
            "subtitles": str(proj.get_path("audio") / "ep01.srt")})
        s = orch.state_mgr.load("ep01")
        assert s["audio"]["subtitle_file"] == "06_音频/ep01.srt"
