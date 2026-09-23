"""review.py 单元测试 — 意图解析规则 + 文件通道往返"""

import pytest

from drama.review import FileReviewChannel, parse_review_reply


class TestParseReviewReply:
    def test_simple_approve(self):
        assert parse_review_reply("通过")["decision"] == "approve"
        assert parse_review_reply("没问题，可以")["decision"] == "approve"
        assert parse_review_reply("OK")["decision"] == "approve"
        assert parse_review_reply("LGTM 👍")["decision"] == "approve"

    def test_reject_with_target(self):
        v = parse_review_reply("打回 镜头03 手不对")
        assert v["decision"] == "reject"
        assert v["targets"] == ["3"]   # _SHOT_RE 剥前导零
        assert "手不对" in v["reason"]

    def test_reject_keyword_beats_approve(self):
        """同时出现通过/打回时，保守取 reject"""
        v = parse_review_reply("镜头03 通过了，但镜头05 打回重做")
        assert v["decision"] == "reject"

    def test_ambiguous_is_conservative_reject(self):
        """识别不到明确"通过"→ 保守判 reject，不误放行"""
        for text in ("画面凑合吧", "再看看", "", "改改节奏"):
            v = parse_review_reply(text)
            assert v["decision"] == "reject", f"'{text}' 应保守判 reject"
            assert v["raw"] == text.strip()

    def test_shot_target_variants(self):
        assert parse_review_reply("打回 shot02")["targets"] == ["2"]
        assert parse_review_reply("第 3 镜重画")["targets"] == ["3"]


class TestFileReviewChannel:
    def test_roundtrip(self, tmp_path):
        ch = FileReviewChannel(tmp_path / "reviews")
        key = "ep01:director"

        assert ch.poll(key) is None            # 未提交
        ch.post(key, "待审包内容")
        assert ch.poll(key) is None            # post 只写 pending，不产生回复
        ch.submit(key, "通过")
        assert ch.poll(key) == "通过"
        ch.ack(key)
        assert ch.poll(key) is None            # ack 后消费掉

    def test_clear_removes_all_residue(self, tmp_path):
        """reset_review 依赖 clear 清残留；必须走公开接口（回归：抽象泄漏修复）"""
        ch = FileReviewChannel(tmp_path / "reviews")
        key = "ep02:director"
        ch.post(key, "pkg")
        ch.submit(key, "打回")
        ch.ack(key)                            # reply → done
        ch.clear(key)
        assert ch.poll(key) is None
        # clear 之后可以重新走完整流程（复活语义）
        ch.post(key, "pkg2")
        ch.submit(key, "通过")
        assert ch.poll(key) == "通过"

    def test_key_colon_sanitized(self, tmp_path):
        ch = FileReviewChannel(tmp_path / "reviews")
        ch.submit("ep01:director", "ok")
        files = list((tmp_path / "reviews").glob("ep01*"))
        assert len(files) == 1 and ":" not in files[0].name

    def test_base_class_clear_is_abstract(self):
        """基类 clear 无实现（抛 NotImplementedError）—— 防止调用方依赖某个具体 provider 的细节"""
        from drama.review import ReviewChannel
        with pytest.raises(NotImplementedError):
            ReviewChannel().clear("ep01:director")
