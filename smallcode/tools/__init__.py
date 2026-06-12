"""
工具注册表模块 — 管理所有工具的定义、注册与调度。

本模块是 AI 代理可调用工具的集中管理点，提供三个核心功能：

1. ``TOOLS`` 字典：将工具名称映射为 ``(描述, 参数模式, 实现函数)`` 三元组，
   是工具定义的单一日誌源（single source of truth）。

2. ``run_tool(name, args)``：根据名称查找并执行工具函数，统一捕获异常。

3. ``make_schema()``：将 ``TOOLS`` 字典转换为 Tool Use API
   所需的 JSON Schema 格式列表，供 API 请求中的 ``tools`` 参数使用。
"""

from typing import Any, Callable, Dict, List, Tuple

# 导入各个工具模块的实现函数
from smallcode.tools.file_tools import bash, edit, glob, grep, read, write
from smallcode.tools.web_fetch import web_fetch
from smallcode.tools.web_search import web_search

# ── 工具注册表 ─────────────────────────────────────────────────────────────────
# 每个条目：(描述文本, 参数字典 {名称: 类型}, 实现函数)
TOOLS: Dict[str, Tuple[str, Dict[str, str], Callable]] = {
    "read": (
        "读取文件内容并显示行号（接受文件路径，不支持目录）",
        {"path": "string", "offset": "number?", "limit": "number?"},
        read,
    ),
    "write": (
        "将内容写入文件（UTF-8 编码）",
        {"path": "string", "content": "string"},
        write,
    ),
    "edit": (
        "在文件中查找并替换文本（旧文本必须唯一，除非指定 all=true）",
        {"path": "string", "old": "string", "new": "string", "all": "boolean?"},
        edit,
    ),
    "glob": (
        "按通配符模式查找文件，结果按修改时间排序",
        {"pat": "string", "path": "string?"},
        glob,
    ),
    "grep": (
        "在文件中搜索文本模式，返回匹配行及上下文。\n"
        "默认使用正则匹配（若含大写字母则区分大小写，否则忽略大小写）。\n"
        "如果正则编译失败，自动降级为字面量搜索。\n"
        "用于查找函数、变量、字符串或 UI 元素的定义与使用位置。",
        {"pat": "string", "path": "string?", "context": "number?", "max_results": "number?"},
        grep,
    ),
    "bash": (
        "执行 Shell 命令并返回输出",
        {"cmd": "string"},
        bash,
    ),
    "web_search": (
        "搜索互联网获取信息，返回标题、URL 和摘要。\n"
        "当你需要查找文档、查询 API、调研库或获取本地不存在的资料时使用。",
        {"query": "string", "max_results": "number?"},
        web_search,
    ),
    "web_fetch": (
        "获取网页内容并以 Markdown 格式返回。\n"
        "在 web_search 之后使用，以读取具体页面（文档、README、源码文件、API 参考）。\n"
        "HTML 默认转换为 Markdown——传递 format='text' 获取纯文本，format='html' 获取原始 HTML。\n"
        "仅允许 http:// 和 https:// 协议；自动阻止对 localhost、私有网络和云元数据端点的请求。",
        {"url": "string", "format": "string?", "max_chars": "number?"},
        web_fetch,
    ),
}


def run_tool(name: str, args: Dict[str, Any]) -> str:
    """
    根据工具名称查找并执行对应工具函数。

    从 ``TOOLS`` 注册表中查找名为 ``name`` 的条目，
    提取实现函数并以 ``args`` 为参数调用。

    所有异常被捕获并格式化为以 ``"error: "`` 开头的字符串返回，
    确保 AI 代理的 tool_use 循环不会因未处理异常而中断。

    参数:
        name: 工具名称（如 ``"read"``、``"grep"``）。
        args: 参数字典，键值对与工具的参数模式匹配。

    返回:
        工具执行的输出字符串，或 ``"error: <异常信息>"``。
    """
    try:
        return TOOLS[name][2](args)
    except Exception as err:
        return f"error: {err}"


def make_schema() -> List[Dict[str, Any]]:
    """
    生成 Tool Use API 兼容的工具模式列表。

    遍历 ``TOOLS`` 注册表，将每个工具的描述和参数模式
    转换为 ``tools`` 参数格式。

    参数类型支持：
    - ``"string"`` → ``{"type": "string"}``
    - ``"number"`` → ``{"type": "integer"}``
    - ``"boolean"`` → ``{"type": "boolean"}``
    - 以 ``?`` 结尾为可选参数（不加入 ``required`` 列表）

    返回:
        符合 Tool Use API 格式的模式字典列表。
    """
    result: List[Dict[str, Any]] = []
    for name, (description, params, _fn) in TOOLS.items():
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for param_name, param_type in params.items():
            is_optional = param_type.endswith("?")
            base_type = param_type.rstrip("?")
            properties[param_name] = {
                "type": "integer" if base_type == "number" else base_type
            }
            if not is_optional:
                required.append(param_name)
        result.append(
            {
                "name": name,
                "description": description,
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            }
        )
    return result
