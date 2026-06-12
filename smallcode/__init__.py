"""
smallcode — 轻量级 AI 编码代理。

提供文件读写、搜索、编辑、Shell 命令执行、网页搜索与抓取，
以及基于 DeepSeek API 的智能对话能力。

使用方式：
    python -m smallcode          # 启动交互式 REPL
    python -c "import smallcode; smallcode.main()"  # 同上
"""

from smallcode.ui import main

__all__ = ["main"]
