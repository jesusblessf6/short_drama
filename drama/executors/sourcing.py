"""Sourcing Executor — 古籍抓取

从公开古籍网站抓取公版作品文本，存到 corpus/ 目录。
支持 ctext.org (中国哲学书电子化计划) 等数据源。
"""

import logging
import re
from pathlib import Path

import httpx
from .base import BaseExecutor

logger = logging.getLogger(__name__)


class SourcingExecutor(BaseExecutor):
    """古籍抓取执行器"""

    def validate_input(self, task: dict) -> bool:
        return "source" in task or "url" in task

    def run(self, task: dict) -> dict:
        """
        task 格式:
            source: "聊斋志异"   # 或 url: "https://ctext.org/..."
            output_dir: "corpus/聊斋志异"
        """
        source = task.get("source", "")
        url = task.get("url", "")
        output_dir = Path(task.get("output_dir", f"corpus/{source}"))
        output_dir.mkdir(parents=True, exist_ok=True)

        if not url:
            # 从 config 的 corpus_sources 查找
            for s in self.config.corpus_sources:
                if s["name"] == source:
                    url = s["url"]
                    break

        if not url:
            return {"success": False, "error": f"未找到数据源: {source}"}

        try:
            # TODO: 实现具体的抓取逻辑
            # ctext.org 有 API: https://ctext.org/api
            # 这里先写骨架，实际实现需要根据具体 API 调整
            logger.info(f"从 {url} 抓取 {source}...")

            # 示例：抓取页面文本（需要根据实际API调整）
            # resp = httpx.get(url, timeout=30)
            # texts = self._parse_ctext(resp.text)

            # 临时：返回未实现
            return {
                "success": False,
                "error": "sourcing 尚未实现具体抓取逻辑，请手动准备语料文件",
                "output_dir": str(output_dir),
            }

        except Exception as e:
            logger.error(f"抓取失败: {e}")
            return {"success": False, "error": str(e)}

    def _parse_ctext(self, html: str) -> list[dict]:
        """解析 ctext.org 页面，提取篇名和正文"""
        # TODO: 实现 HTML 解析
        pass

    def save_text(self, output_dir: Path, title: str, content: str) -> Path:
        """保存单篇文本"""
        safe_title = re.sub(r'[\\/:*?"<>|]', "_", title)
        path = output_dir / f"{safe_title}.txt"
        path.write_text(content, encoding="utf-8")
        return path
