"""
网页搜索模块 — 通过 DuckDuckGo HTML 接口搜索互联网。

本模块使用 DuckDuckGo 的非官方 HTML 端点（``https://html.duckduckgo.com/html/``）
执行搜索，直接解析返回的 HTML 页面提取搜索结果。
这种方式不需要 API Key，但依赖简单的 HTML 解析。

模块结构：
- ``web_search(args)`` — 公开工具函数，供 AI 代理调用
- ``_strip_html_tags(s)`` — 去除 HTML 标签并解码实体
- ``_url_decode(s)`` — 简单的 URL 百分比解码
- ``_extract_ddg_url(raw)`` — 从 DuckDuckGo 重定向 URL 中提取真实网址
- ``_parse_ddg_results(html, max_results)`` — 解析 DuckDuckGo 搜索结果 HTML
"""

import subprocess
import sys
import urllib.parse
from typing import Any, Dict, List, Optional

# DuckDuckGo HTML 搜索端点
DDG_HTML_URL = "https://html.duckduckgo.com/html/"


# ── HTML 辅助函数 ──────────────────────────────────────────────────────────────


def _strip_html_tags(s: str) -> str:
    """
    去除字符串中的 HTML 标签并解码常见 HTML 实体。

    功能：
    - 移除所有 ``<tag>`` / ``</tag>`` 标签
    - 解码 ``&amp;``、``&lt;``、``&gt;``、``&quot;``、``&#x27;``、``&#39;``、``&nbsp;``

    参数:
        s: 包含 HTML 标签的原始字符串。

    返回:
        纯文本字符串。
    """
    result: List[str] = []
    in_tag = False
    for c in s:
        if c == '<':
            in_tag = True
        elif c == '>':
            in_tag = False
        elif not in_tag:
            result.append(c)
    text = ''.join(result)

    # 解码常见 HTML 实体
    replacements = [
        ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", "\""), ("&#x27;", "'"), ("&#39;", "'"),
        ("&nbsp;", " "),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def _url_decode(s: str) -> str:
    """
    简单的 URL 百分比解码。

    将 ``%XX`` 形式的编码转换为对应字符，将 ``+`` 转换为空格。
    若某组 ``%XX`` 不是合法的十六进制转义，则保留原样。

    参数:
        s: URL 编码的字符串。

    返回:
        解码后的字符串。
    """
    result: List[str] = []
    i = 0
    while i < len(s):
        if s[i] == '%' and i + 2 < len(s):
            try:
                result.append(chr(int(s[i + 1:i + 3], 16)))
                i += 3
                continue
            except ValueError:
                pass
        elif s[i] == '+':
            result.append(' ')
        else:
            result.append(s[i])
        i += 1
    return ''.join(result)


def _extract_ddg_url(raw: str) -> str:
    """
    从 DuckDuckGo 的重定向 URL 中提取真实目标网址。

    DuckDuckGo 的搜索结果链接形如 ``//duckduckgo.com/l/?uddg=https%3A%2F%2F...``
    本函数提取 ``uddg`` 参数的值并执行 URL 解码。

    参数:
        raw: 原始 URL 字符串（可能包含 DuckDuckGo 跟踪参数）。

    返回:
        解码后的真实 URL。
    """
    uddg_pos = raw.find("uddg=")
    if uddg_pos >= 0:
        start = uddg_pos + 5
        end = raw.find('&', start)
        if end < 0:
            end = len(raw)
        return _url_decode(raw[start:end])
    if raw.startswith("http"):
        return raw
    if raw.startswith("//"):
        return "https:" + raw
    return raw


def _parse_ddg_results(html: str, max_results: int) -> List[Dict[str, str]]:
    """
    解析 DuckDuckGo HTML 搜索结果页面，提取标题、URL 和摘要。

    解析策略（基于 HTML 结构特征）：
    1. 查找 ``class="result__a"`` 标记定位每个结果
    2. 回溯到最近的 ``<a`` 标签获取链接
    3. 提取连接文本作为标题
    4. 查找后续的 ``class="result__snippet"`` 获取摘要

    参数:
        html: DuckDuckGo 返回的完整 HTML 页面。
        max_results: 最大返回结果数（1-20）。

    返回:
        字典列表，每项包含 ``title``、``url``、``snippet`` 三个键。
    """
    results: List[Dict[str, str]] = []
    pos = 0
    link_marker = 'class="result__a"'

    while len(results) < max_results:
        # 定位下一个结果标记
        marker_pos = html.find(link_marker, pos)
        if marker_pos < 0:
            break
        after_marker = marker_pos + len(link_marker)

        # 向左搜索最近的 '<a' 标签起始位置
        tag_start = html.rfind('<', 0, marker_pos)
        if tag_start < 0:
            tag_start = marker_pos

        # 向右搜索 '</a>' 标签结束位置
        tag_end = html.find('</a>', after_marker)
        if tag_end < 0:
            tag_end = after_marker
        tag_end_plus_4 = tag_end + 4

        # 提取该结果区域的完整 HTML
        tag_region = html[tag_start:tag_end_plus_4]

        # 提取 href 属性值
        hp = tag_region.find('href="')
        if hp < 0:
            pos = tag_end_plus_4
            continue
        hs = hp + 6
        he = tag_region.find('"', hs)
        if he < 0:
            he = hs
        url = _extract_ddg_url(tag_region[hs:he])

        # 提取标题文本（``>`` 和 ``</a>`` 之间的内容）
        content_start = html.find('>', after_marker, tag_end)
        if content_start >= 0:
            content_start += 1
        else:
            content_start = after_marker
        title = _strip_html_tags(html[content_start:tag_end]) if content_start <= tag_end else ""

        # 提取摘要（``class="result__snippet"`` 中的文本）
        snippet_end_search = min(tag_end + 2000, len(html))
        snippet_marker = 'class="result__snippet"'
        sp = html.find(snippet_marker, tag_end, snippet_end_search)
        snippet = ""
        if sp >= 0:
            s_start = html.find('>', sp)
            if s_start >= 0:
                s_start += 1
                s_end = html.find('</a>', s_start)
                if s_end >= 0:
                    snippet = _strip_html_tags(html[s_start:s_end])

        # 仅保留有标题、有合法 HTTP URL 的结果
        if title.strip() and url and url.startswith("http"):
            results.append({
                "title": title.strip(),
                "url": url,
                "snippet": snippet.strip(),
            })

        pos = tag_end_plus_4

    return results


# ── 公开搜索工具 ──────────────────────────────────────────────────────────────


def web_search(args: Dict[str, Any]) -> str:
    """
    通过 DuckDuckGo 搜索互联网并返回格式化结果。

    使用 ``curl`` 命令行工具向 DuckDuckGo HTML 端点发送 POST 请求，
    解析返回的 HTML 页面提取标题、URL 和摘要。

    支持参数：
    - ``query``（必需）: 搜索关键词
    - ``max_results``（可选，默认 8，最大 20）: 返回的最大结果数

    返回:
        格式化后的搜索结果字符串，每行格式为：
        ``序号. 标题``
        ``   URL``
        ``   摘要``

        失败时返回以 ``"error: "`` 开头的错误描述。
    """
    query = args.get("query", "").strip()
    max_results = min(args.get("max_results", 8), 20)

    if not query:
        return "error: 搜索关键词 (query) 不能为空"

    # 使用 curl 发送 POST 请求 — DuckDuckGo 对 Python urllib 的 TLS 指纹有检测限制
    curl_bin = "curl.exe" if sys.platform == "win32" else "curl"
    q_encoded = query.replace(' ', '+')

    try:
        proc = subprocess.run(
            [curl_bin, "-s", "-X", "POST", DDG_HTML_URL,
             "-d", f"q={q_encoded}",
             "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)",
             "--max-time", "15", "-L"],
            capture_output=True, timeout=20,
        )
        html = (proc.stdout or b"").decode("utf-8", errors="replace")
    except FileNotFoundError:
        return "error: 未找到 curl 命令——请安装 curl 或直接使用 web_get 工具"
    except subprocess.TimeoutExpired:
        return f"error: 搜索在 20 秒后超时 (关键词: '{query}')。网络可能不可达或 DuckDuckGo 响应缓慢。"
    except Exception as e:
        return f"error: 搜索失败: {e}"

    if not html.strip():
        return f"error: 搜索返回空响应 (关键词: '{query}')"

    results = _parse_ddg_results(html, max_results)
    if not results:
        return f"未找到 '{query}' 的搜索结果（收到 {len(html)} 字节 HTML）"

    # 格式化输出
    out = [f'"{query}" 的搜索结果:\n']
    for i, r in enumerate(results):
        out.append(f"{i + 1}. {r['title']}\n   {r['url']}\n   {r['snippet']}\n")
    return "\n".join(out)
