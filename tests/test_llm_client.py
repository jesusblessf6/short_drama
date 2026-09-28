"""LLMClient 回归 — 推理模型 <think> 思考块清理（MiniMax M2 系列等）

不清理的后果（真实踩过）：剧本文件混入英文推理、storyboard 的「### 镜头NN」
结构化解析被思考文本干扰。对不含该标签的 provider（GLM 等）零影响。
"""

import httpx

from drama.config import LLMConfig
from drama.llm import LLMClient, strip_thinking

from conftest import make_orchestrator


class TestStripThinking:
    def test_strips_full_block(self):
        raw = "<think>\n用户要一句台词。\n好，输出。\n</think>\n“苍天在上！”"
        assert strip_thinking(raw) == "“苍天在上！”"

    def test_strips_truncated_block(self):
        """输出被 max_tokens 截断、闭标记缺失：丢弃残缺思考块"""
        raw = "<think>\n用户让我说台词，我应该写一句古风对白，符合角色身份和时代背景特点，"
        assert strip_thinking(raw) == ""

    def test_keeps_normal_text_untouched(self):
        for raw in ("普通回复", "", None):
            assert strip_thinking(raw) == (raw or "")

    def test_unterminated_tag_drops_tail_conservatively(self):
        """只有开标记（截断形态）→ 丢弃其后全部：宁可少输出，不混入思考文本污染剧本"""
        assert strip_thinking("含有类似 <think> 字样的正文吗") == "含有类似"
        assert strip_thinking("前半段正常<think>思考") == "前半段正常"

    def test_multiline_and_multiblock(self):
        raw = "<think>a\nb</think>\n中间<think>c</think>结尾"
        assert strip_thinking(raw) == "中间结尾"

    def test_client_chat_strips(self, monkeypatch):
        """chat() 返回值已清理（其他 provider 无标签时行为不变）"""
        cfg = LLMConfig(provider="minimax", base_url="https://api.minimax.cn/v1",
                        api_key="k", model="MiniMax-M2.1", vision_model="m",
                        max_tokens=1024, temperature=0.7)
        client = LLMClient(cfg)

        def fake_create(**kwargs):
            msg = type("M", (), {"content": "<think>思考</think>\n正文"})()
            resp = type("R", (), {"usage": type("U", (), {"total_tokens": 9})(),
                                   "choices": [type("C", (), {"message": msg})()]})()
            return resp

        monkeypatch.setattr(client.client.chat.completions, "create", fake_create)
        assert client.chat([{"role": "user", "content": "x"}]) == "正文"
        assert client.last_usage == 9


class TestConfigWithMinimaxLLM:
    def test_real_llm_mode_active(self, tmp_path):
        """配置了 key → is_offline False（创意层走真实 LLM 而非离线模板）"""
        from drama.config import Config
        cfg = Config.from_yaml("/Users/wing/mySpace/short_drama/config.yaml")
        assert cfg.llm.provider == "minimax"
        assert cfg.llm.base_url == "https://api.minimax.cn/v1"
        assert cfg.llm.max_tokens >= 4096     # M2 思考块占 token，max_tokens 不能太小

    def test_offline_when_key_empty(self, tmp_path):
        orch, _ = make_orchestrator(tmp_path)
        assert orch.config.llm.is_offline is True    # 测试夹具零 key 仍离线
