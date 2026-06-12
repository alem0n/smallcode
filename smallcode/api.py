"""
API 通信模块 — 向 DeepSeek API 发送请求并接收响应。

本模块封装了与 DeepSeek API 的 HTTP 通信逻辑。
主要职责：
- 构建符合 Messages API 格式的请求体
- 在请求中附加工具模式定义（通过 ``make_schema``）
- 发送 HTTP POST 请求并解析 JSON 响应

``call_api`` 被 ``ui.py`` 中的 REPL 主循环调用，是 AI 对话的核心网络层。
"""

import json
import os
import urllib.request
from typing import Any, Dict, List

from smallcode.config import API_URL, DEEPSEEK_API_KEY, MODEL
from smallcode.tools import make_schema


def call_api(
    messages: List[Dict[str, Any]],
    system_prompt: str,
) -> Dict[str, Any]:
    """
    向 DeepSeek API 发送消息列表并获取响应。

    根据 ``DEEPSEEK_API_KEY`` 的存在与否自动选择认证头：
    - 有 Key 时使用 ``Authorization: Bearer <key>``
    - 无 Key 时使用 ``x-api-key``（兼容 x-api-key 认证方式）

    参数:
        messages: 消息历史列表，每一条包含 ``role`` 和 ``content``。
        system_prompt: 系统提示词字符串，描述 AI 助手的角色与行为。

    返回:
        API 响应字典，包含 ``content``（消息块列表）、``stop_reason`` 等字段。

    抛出:
        urllib.error.URLError / HTTPError: 网络或服务端错误（由调用方处理）。
        json.JSONDecodeError: API 返回了非 JSON 响应（由调用方处理）。
    """
    request = urllib.request.Request(
        API_URL,
        # 序列化请求体：包含模型、最大 token 数、系统提示、消息历史和工具模式
        data=json.dumps(
            {
                "model": MODEL,
                "max_tokens": 8192,
                "system": system_prompt,
                "messages": messages,
                "tools": make_schema(),
            }
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "anthropic-version": "2026-06-01",
            # 根据 Key 类型选择认证头
            **(
                {"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
                if DEEPSEEK_API_KEY
                else {"x-api-key": os.environ.get("ANTHROPIC_API_KEY", "")}
            ),
        },
    )
    response = urllib.request.urlopen(request)
    return json.loads(response.read())
