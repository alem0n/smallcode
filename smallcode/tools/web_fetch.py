"""
网页抓取模块 — 安全的 HTTP 内容获取与 HTML→Markdown/Text 格式转换。

本模块提供安全的网页内容抓取能力，包含多层安全防护：
1. **SSRF 防护**：通过 ``_is_safe_ip`` 和 ``_validate_host`` 阻止对内网、
   本地回环、云元数据端点等敏感地址的连接
2. **手动重定向处理**：逐跳重新验证目标，防止跳转到恶意地址
3. **字节上限**：2 MiB 读取上限，防止内存耗尽攻击

同时提供完整的 HTML→Markdown 转换管线，支持代码块、链接、列表、表格等。

模块结构：
- 安全层：``_is_safe_ip`` → ``_validate_host`` → ``_NoRedirectHandler``
- HTML 工具：``_remove_tag_content`` → ``_replace_tag_with``
- 格式转换：``_html_to_text`` → ``_html_to_markdown`` → ``_strip_to_dominant_code_block``
- 公开工具：``web_fetch(args)`` - 统一入口
"""

import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from smallcode.encoding import try_decode_text

# ── 全局常量 ───────────────────────────────────────────────────────────────────

WEB_FETCH_MAX_BYTES: int = 2 * 1024 * 1024       # 2 MiB 读取上限
WEB_FETCH_MAX_REDIRECTS: int = 5                   # 最大重定向次数
WEB_FETCH_TIMEOUT: int = 20                         # 总体超时（秒）
WEB_FETCH_CONNECT_TIMEOUT: int = 5                  # 连接超时（秒）
WEB_FETCH_UA: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/143.0.0.0 Safari/537.36"
)


# ══════════════════════════════════════════════════════════════════════════════
# 第一部分：SSRF 防护
# ══════════════════════════════════════════════════════════════════════════════


def _is_safe_ip(ip_str: str) -> Optional[str]:
    """
    检查 IP 地址是否指向安全的外部网络。

    拒绝规则（与 Rust 的 ``std::net::Ipv4Addr::is_private()`` 等标准对齐）：
    IPv4:
    - 回环 ``127.0.0.0/8``
    - 私有网络 ``10.0.0.0/8``、``172.16.0.0/12``、``192.168.0.0/16``
    - 链路本地 ``169.254.0.0/16``
    - 多播 ``224.0.0.0/4``
    - 广播 ``255.255.255.255``
    - 未指定 ``0.0.0.0``
    - 保留段 ``0.0.0.0/8``、``240.0.0.0/4``
    - CGNAT ``100.64.0.0/10``
    IPv6:
    - 回环 ``::1``、未指定 ``::``、多播
    - Unique-local ``fc00::/7``、链路本地 ``fe80::/10``
    - IPv4-mapped 地址委派给 IPv4 检查

    参数:
        ip_str: IP 地址字符串（如 ``"127.0.0.1"`` 或 ``"::1"``）。

    返回:
        若 IP 安全则返回 ``None``；若被拒绝则返回人类可读的拒绝原因字符串。
    """
    import ipaddress
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return None

    def reject(reason: str) -> str:
        """生成格式化的拒绝消息。"""
        return f"拒绝连接到 {ip_str}（{reason}）— SSRF 防护"

    if ip.version == 4:
        v4 = ip
        if v4.is_loopback:
            return reject("回环地址 127.0.0.0/8")
        # RFC 1918 私有网络
        if v4 in ipaddress.ip_network('10.0.0.0/8') or \
           v4 in ipaddress.ip_network('172.16.0.0/12') or \
           v4 in ipaddress.ip_network('192.168.0.0/16'):
            return reject("私有网络")
        if v4.is_link_local:
            return reject("链路本地 / 云元数据")
        if v4.is_multicast:
            return reject("多播地址")
        if v4 == ipaddress.IPv4Address('255.255.255.255'):
            return reject("广播地址")
        if v4.is_unspecified:
            return reject("未指定地址 0.0.0.0")
        octets = int(v4).to_bytes(4, 'big')
        if octets[0] == 0:
            return reject("保留段 0.0.0.0/8")
        if octets[0] >= 240:
            return reject("保留段 240.0.0.0/4")
        if octets[0] == 100 and (octets[1] & 0xc0) == 64:
            return reject("CGNAT 100.64/10")
    else:
        v6 = ip
        if v6.is_loopback:
            return reject("回环地址 ::1")
        if v6.is_unspecified:
            return reject("未指定地址 ::")
        if v6.is_multicast:
            return reject("多播地址")
        segs = v6.exploded.split(':')
        first = int(segs[0], 16) if segs[0] else 0
        if (first & 0xfe00) == 0xfc00:
            return reject("Unique-local fc00::/7")
        if (first & 0xffc0) == 0xfe80:
            return reject("链路本地 fe80::/10")
        if v6.ipv4_mapped:
            return _is_safe_ip(str(v6.ipv4_mapped))
    return None


def _validate_host(url_parsed: urllib.parse.ParseResult) -> Optional[str]:
    """
    解析并验证 URL 的主机名，对解析出的所有 IP 进行 SSRF 检查。

    验证流程：
    1. 若主机名是字面 IP → 直接检查
    2. 否则 → DNS 解析（含 scheme 感知的默认端口），逐 IP 检查

    参数:
        url_parsed: ``urllib.parse.urlparse()`` 的解析结果。

    返回:
        验证通过返回 ``None``；失败返回错误描述字符串。
    """
    host = url_parsed.hostname
    if not host:
        return f"URL 缺少主机名: {url_parsed.geturl()}"

    # 字面 IP：直接走 SSRF 检查
    try:
        import ipaddress
        ipaddress.ip_address(host)
        return _is_safe_ip(host)
    except ValueError:
        pass

    # DNS 解析（含 scheme 感知的默认端口）
    try:
        port = url_parsed.port
        if port is None:
            port = 443 if url_parsed.scheme == 'https' else 80
        addrs = socket.getaddrinfo(host, port)
    except socket.gaierror as e:
        return f"DNS 解析失败: `{host}` — {e}"

    saw_any = False
    for addr in addrs:
        ip = addr[4][0]
        saw_any = True
        err = _is_safe_ip(ip)
        if err:
            return err

    if not saw_any:
        return f"DNS 未返回任何地址: `{host}`"
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 第二部分：HTML 解析辅助函数
# ══════════════════════════════════════════════════════════════════════════════


def _remove_tag_content(html_text: str, tag: str) -> str:
    """
    移除 HTML 中指定标签及其全部内容。

    例如移除 ``<script>...</script>``、``<style>...</style>`` 等。
    会进行标签名边界检查（避免将 ``<head`` 与 ``<header`` 混淆）。

    参数:
        html_text: 原始 HTML 字符串。
        tag: 要移除的标签名（不含尖括号，如 ``"script"``）。

    返回:
        移除指定标签及其内容后的 HTML 字符串。
    """
    open_tag = f"<{tag}"
    close_tag = f"</{tag}>"
    result: List[str] = []
    pos = 0
    lower = html_text.lower()

    while True:
        rel = lower.find(open_tag, pos)
        if rel < 0:
            result.append(html_text[pos:])
            break
        abs_start = rel
        # 边界检查：<head 不能匹配 <header
        after = abs_start + len(open_tag)
        if after < len(lower):
            next_char = html_text[after]
            if next_char not in ('>', '/', ' ', '\t', '\n', '\r'):
                result.append(html_text[pos:abs_start + 1])
                pos = abs_start + 1
                continue
        result.append(html_text[pos:abs_start])
        end_pos = lower.find(close_tag, abs_start)
        if end_pos >= 0:
            pos = end_pos + len(close_tag)
        else:
            # 未闭合标签 — 删除到文件末尾
            break

    return ''.join(result)


def _replace_tag_with(html_text: str, tag: str, replacement: str) -> str:
    """
    将 HTML 中指定标签的**开标签**替换为给定字符串。

    例如将所有 ``<p>``、``<div>`` 等替换为 ``\\n``。
    会进行标签名边界检查（避免误替换）。

    参数:
        html_text: 原始 HTML 字符串。
        tag: 要替换的标签名（不含尖括号）。
        replacement: 替换字符串（通常是 ``"\\n"``）。

    返回:
        替换后的 HTML 字符串。
    """
    open_tag = f"<{tag}"
    result: List[str] = []
    pos = 0
    lower = html_text.lower()

    while True:
        rel = lower.find(open_tag, pos)
        if rel < 0:
            result.append(html_text[pos:])
            break
        abs_start = rel
        # 边界检查
        after = abs_start + len(open_tag)
        if after < len(lower):
            next_char = html_text[after]
            if next_char not in ('>', '/', ' ', '\t', '\n', '\r'):
                result.append(html_text[pos:abs_start + 1])
                pos = abs_start + 1
                continue
        result.append(html_text[pos:abs_start])
        result.append(replacement)
        # 跳过整个标签（含属性），直到 '>'
        close = html_text.find('>', abs_start)
        if close >= 0:
            pos = close + 1
        else:
            pos = abs_start + len(open_tag)

    return ''.join(result)


def _html_to_text(html_text: str) -> str:
    """
    将 HTML 转换为可读的纯文本。

    处理流程：
    1. 移除 ``<script>``、``<style>``、``<head>``、``<nav>``、``<footer>``
    2. 块级元素（``<p>``、``<div>`` 等）替换为换行
    3. 去除剩余 HTML 标签
    4. 解码 HTML 实体
    5. 折叠多余空白行

    参数:
        html_text: 原始 HTML 字符串。

    返回:
        清理后的纯文本字符串。
    """
    # Phase 1: 移除无内容标签
    cleaned = _remove_tag_content(html_text, "script")
    cleaned = _remove_tag_content(cleaned, "style")
    cleaned = _remove_tag_content(cleaned, "head")
    cleaned = _remove_tag_content(cleaned, "nav")
    cleaned = _remove_tag_content(cleaned, "footer")

    # Phase 2: 块级元素转换行
    for tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
                "article", "section", "blockquote", "pre", "dd", "dt"):
        cleaned = _replace_tag_with(cleaned, tag, "\n")

    # Phase 3: 去除剩余 HTML 标签
    text_parts: List[str] = []
    in_tag = False
    for c in cleaned:
        if c == '<':
            in_tag = True
        elif c == '>':
            in_tag = False
        elif not in_tag:
            text_parts.append(c)
    text = ''.join(text_parts)

    # Phase 4: 解码 HTML 实体
    entities = [
        ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", "\""),
        ("&#x27;", "'"), ("&#39;", "'"), ("&nbsp;", " "), ("&#x2F;", "/"),
        ("&apos;", "'"), ("&#160;", " "),
    ]
    for old, new in entities:
        text = text.replace(old, new)

    # Phase 5: 清理空白（折叠连续空行）
    lines: List[str] = []
    prev_blank = False
    for line in text.splitlines():
        trimmed = line.strip()
        if not trimmed:
            if not prev_blank and lines:
                lines.append("")
                prev_blank = True
        else:
            lines.append(trimmed)
            prev_blank = False

    # 移除首尾空行
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# 第三部分：HTML → Markdown 转换
# ══════════════════════════════════════════════════════════════════════════════


def _html_to_markdown(html_text: str) -> str:
    """
    将 HTML 转换为 Markdown 格式。

    保留的元素：
    - 围栏代码块（``<pre><code>`` → ````lang\\n...```` ）
    - 链接（``<a>`` → ``[text](url)``）
    - 图片（``<img>`` → ``![alt](src)``）
    - 标题（``<h1>~<h6>`` → ``# ~ ######``）
    - 粗体/斜体/行内代码/删除线
    - 水平线（``<hr>`` → ``---``）

    此外还包含一种智能检测：如果转换后的内容中有一个围栏代码块
    占据了绝大多数篇幅（>55%），则只保留该代码块（适用于 GitHub 源码页等）。

    参数:
        html_text: 原始 HTML 字符串。

    返回:
        Markdown 格式字符串（或纯文本回退）。
    """
    # ── Phase 1: 移除无内容标签 ──────────────────────────────────────────
    cleaned = _remove_tag_content(html_text, "script")
    cleaned = _remove_tag_content(cleaned, "style")
    cleaned = _remove_tag_content(cleaned, "head")
    cleaned = _remove_tag_content(cleaned, "noscript")
    cleaned = _remove_tag_content(cleaned, "iframe")

    # ── Phase 2: 提取并保护围栏代码块 ───────────────────────────────────
    FENCE_MARKER = "\x00FENCE\x00"
    fenced_blocks: List[str] = []

    def _save_fence(m: re.Match) -> str:
        """替换 <pre><code>...</code></pre> 为占位符，同时保存代码块内容。"""
        inner = m.group(1)
        # 提取语言（从 class="language-xxx" 属性中）
        lang = ""
        lm = re.search(r'class="[^"]*\blanguage-(\w+)"', inner)
        if lm:
            lang = lm.group(1)
        # 提取代码文本
        code_start = inner.find('>')
        if code_start >= 0:
            code_start += 1
            code_end = inner.rfind("</code>")
            if code_end >= 0:
                code_text = inner[code_start:code_end]
            else:
                code_text = inner[code_start:]
        else:
            code_text = inner
        # 解码 HTML 实体
        code_text = code_text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        code_text = code_text.replace("&quot;", '"').replace("&#39;", "'").replace("&#x27;", "'")
        fenced_blocks.append(f"```{lang}\n{code_text}\n```")
        return FENCE_MARKER

    cleaned = re.sub(
        r'<pre(?:\s[^>]*)?>(.*?)</pre>',
        _save_fence,
        cleaned,
        flags=re.I | re.S,
    )

    # ── Phase 3: 转换块级元素 ────────────────────────────────────────────
    # 标题
    cleaned = re.sub(r'<h1(?:\s[^>]*)?>(.*?)</h1>', r'# \1\n\n', cleaned, flags=re.I | re.S)
    cleaned = re.sub(r'<h2(?:\s[^>]*)?>(.*?)</h2>', r'## \1\n\n', cleaned, flags=re.I | re.S)
    cleaned = re.sub(r'<h3(?:\s[^>]*)?>(.*?)</h3>', r'### \1\n\n', cleaned, flags=re.I | re.S)
    cleaned = re.sub(r'<h4(?:\s[^>]*)?>(.*?)</h4>', r'#### \1\n\n', cleaned, flags=re.I | re.S)
    cleaned = re.sub(r'<h5(?:\s[^>]*)?>(.*?)</h5>', r'##### \1\n\n', cleaned, flags=re.I | re.S)
    cleaned = re.sub(r'<h6(?:\s[^>]*)?>(.*?)</h6>', r'###### \1\n\n', cleaned, flags=re.I | re.S)
    # 水平线
    cleaned = re.sub(r'<hr\s*/?>', '\n---\n\n', cleaned, flags=re.I)

    # ── Phase 4: 转换行内元素 ────────────────────────────────────────────
    # 图片（需在链接之前处理，避免匹配到 <img 内部的链接）
    cleaned = re.sub(
        r'<img(?:\s[^>]*)?src="([^"]*)"(?:\s[^>]*)?alt="([^"]*)"(?:\s[^>]*)?/?>',
        r'![\2](\1)',
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(
        r'<img(?:\s[^>]*)?src="([^"]*)"(?:\s[^>]*)?/?>',
        r'![](\1)',
        cleaned,
        flags=re.I,
    )
    # 链接
    cleaned = re.sub(
        r'<a(?:\s[^>]*)?href="([^"]*)"(?:\s[^>]*)?>(.*?)</a>',
        r'[\2](\1)',
        cleaned,
        flags=re.I | re.S,
    )
    # 粗体
    cleaned = re.sub(r'<(?:strong|b)(?:\s[^>]*)?>(.*?)</(?:strong|b)>', r'**\1**', cleaned, flags=re.I | re.S)
    # 斜体
    cleaned = re.sub(r'<(?:em|i)(?:\s[^>]*)?>(.*?)</(?:em|i)>', r'*\1*', cleaned, flags=re.I | re.S)
    # 行内代码
    cleaned = re.sub(r'<code(?:\s[^>]*)?>(.*?)</code>', r'`\1`', cleaned, flags=re.I | re.S)
    # 删除线
    cleaned = re.sub(r'<del(?:\s[^>]*)?>(.*?)</del>', r'~~\1~~', cleaned, flags=re.I | re.S)

    # ── Phase 5: 块级标签转换行 ──────────────────────────────────────────
    for tag in ("p", "div", "br", "li", "tr", "article", "section",
                "blockquote", "pre", "dd", "dt"):
        cleaned = _replace_tag_with(cleaned, tag, "\n")

    # ── Phase 6: 去除剩余 HTML 标签 ──────────────────────────────────────
    md_parts: List[str] = []
    in_tag = False
    for c in cleaned:
        if c == '<':
            in_tag = True
        elif c == '>':
            in_tag = False
        elif not in_tag:
            md_parts.append(c)
    md = ''.join(md_parts)

    # ── Phase 7: 解码 HTML 实体 ──────────────────────────────────────────
    entities = [
        ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", "\""),
        ("&#x27;", "'"), ("&#39;", "'"), ("&nbsp;", " "), ("&#x2F;", "/"),
        ("&apos;", "'"), ("&#160;", " "), ("&ndash;", "–"), ("&mdash;", "—"),
        ("&hellip;", "…"), ("&copy;", "©"), ("&reg;", "®"),
    ]
    for old, new in entities:
        md = md.replace(old, new)

    # ── Phase 8: 折叠连续空行 ────────────────────────────────────────────
    lines: List[str] = []
    prev_blank = False
    for line in md.splitlines():
        trimmed = line.strip()
        if not trimmed:
            if not prev_blank and lines:
                lines.append("")
                prev_blank = True
        else:
            lines.append(trimmed)
            prev_blank = False
    # 去除首尾空行
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    md = "\n".join(lines)

    # ── Phase 9: 恢复保护的围栏代码块 ────────────────────────────────────
    for block in fenced_blocks:
        md = md.replace(FENCE_MARKER, block, 1) if FENCE_MARKER in md else md

    # ── Phase 10: 智能代码块提取 ─────────────────────────────────────────
    stripped = _strip_to_dominant_code_block(md)
    return stripped if stripped else md


def _strip_to_dominant_code_block(md: str) -> Optional[str]:
    """
    如果 Markdown 中有一个围栏代码块占据绝对优势，仅保留该代码块。

    判断条件：
    - 代码块行数 >= 15 行
    - 代码块字节数占总内容 >= 55%

    这适用于 GitHub 等源码浏览页面，其中大多数内容是代码而非文本说明。

    参数:
        md: 完整的 Markdown 字符串。

    返回:
        若存在主导代码块，返回该代码块内容；否则返回 ``None``。
    """
    MIN_BLOCK_LINES = 15
    MIN_BLOCK_PERCENT = 55

    total = len(md.strip())
    if total == 0:
        return None

    lines = md.splitlines()
    # 找出所有以 ``` 开头的行
    fence_starts = [i for i, l in enumerate(lines) if l.strip().startswith("```")]

    best: Optional[Tuple[int, int, int]] = None  # (start, end_inclusive, bytes)

    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("```"):
            start = i
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                j += 1
            end = min(j, len(lines) - 1)
            block_bytes = sum(len(lines[k]) + 1 for k in range(start, end + 1))
            if best is None or block_bytes > best[2]:
                best = (start, end, block_bytes)
            i = end + 1
        else:
            i += 1

    if best is None:
        return None

    start, end, block_bytes = best
    block_lines = end - start + 1
    if block_lines >= MIN_BLOCK_LINES and block_bytes * 100 >= total * MIN_BLOCK_PERCENT:
        return "\n".join(lines[start:end + 1])
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 第四部分：渲染与输出辅助
# ══════════════════════════════════════════════════════════════════════════════


def _render_body(fmt: str, is_html: bool, body: str) -> str:
    """
    根据请求的格式渲染获取到的页面主体。

    非 HTML 内容按原样返回（无论 fmt 如何设置）。
    HTML 内容的渲染策略：
    - ``"html"`` → 原始 HTML
    - ``"markdown"`` → 调用 ``_html_to_markdown``
    - 其他（``"text"``） → 调用 ``_html_to_text``

    参数:
        fmt: 请求的输出格式（``"markdown"``、``"text"``、``"html"``）。
        is_html: 内容是否为 HTML。
        body: 页面主体文本。

    返回:
        渲染后的字符串。
    """
    if fmt == "html":
        return body
    if not is_html:
        return body
    if fmt == "markdown":
        return _html_to_markdown(body)
    return _html_to_text(body)


def _apply_char_cap(text: str, max_chars: Optional[int]) -> str:
    """
    对文本应用可选的字符数上限。

    若 ``max_chars`` 不为 ``None`` 且文本超出限制，
    在截断位置添加截断标记。

    由于 Python 字符串为 Unicode 原生，按字符切片是安全的。

    参数:
        text: 输入文本。
        max_chars: 最大字符数。若为 ``None`` 则不截断。

    返回:
        截断后（或原始）的文本。
    """
    if max_chars is not None and len(text) > max_chars:
        return f"{text[:max_chars]}\n\n[已截断至 {max_chars} 字符，原文 {len(text)} 字符]"
    return text


# ══════════════════════════════════════════════════════════════════════════════
# 第五部分：HTTP 请求基础设施
# ══════════════════════════════════════════════════════════════════════════════


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """
    不自动跟随重定向的 HTTP 处理器。

    我们手动处理重定向，以便逐跳重新进行 SSRF 检查和 scheme 验证。
    """

    def http_error_302(self, req, fp, code, msg, headers):
        """捕获 302 状态码，直接返回响应而不跟随重定向。"""
        return fp

    # 所有 3xx 状态码使用同一处理
    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


# 使用自定义无重定向处理器构建 opener
_no_redirect_opener = urllib.request.build_opener(_NoRedirectHandler)
_no_redirect_opener.addheaders = [("User-Agent", WEB_FETCH_UA)]


# ══════════════════════════════════════════════════════════════════════════════
# 第六部分：公开抓取工具
# ══════════════════════════════════════════════════════════════════════════════


def web_fetch(args: Dict[str, Any]) -> str:
    """
    获取网页内容并以 Markdown（默认）、纯文本或原始 HTML 格式返回。

    安全特性：
    1. 只允许 ``http://`` 和 ``https://`` 协议
    2. 逐跳 SSRF 防护（DNS 解析 + IP 过滤）
    3. 手动重定向处理（最多 5 跳）
    4. 2 MiB 流式读取上限
    5. 非 ASCII Location 头拒绝

    支持参数：
    - ``url``（必需）: 要获取的完整 URL
    - ``format``（可选，默认 ``"markdown"``）: 输出格式（``"markdown"`` | ``"text"`` | ``"html"``）
    - ``max_chars``（可选）: 最大字符数

    返回:
        格式化后的页面内容字符串，或错误描述。
    """
    url_str = args.get("url", "").strip()
    fmt = args.get("format", "markdown").lower()
    max_chars = args.get("max_chars")

    # ── 参数验证 ─────────────────────────────────────────────────────────
    if not url_str:
        return "error: url 参数不能为空"
    if fmt not in ("markdown", "text", "html"):
        fmt = "markdown"

    # ── URL 解析 ─────────────────────────────────────────────────────────
    try:
        parsed = urllib.parse.urlparse(url_str)
    except Exception as e:
        return f"error: 无效 URL: {e}"

    # ── 手动重定向循环（逐跳验证） ───────────────────────────────────────
    hops = 0
    current_url = url_str
    response = None

    while hops <= WEB_FETCH_MAX_REDIRECTS:
        cur_parsed = urllib.parse.urlparse(current_url)

        # Scheme 验证
        if cur_parsed.scheme not in ("http", "https"):
            return f"error: 禁止的协议 `{cur_parsed.scheme}` — 仅允许 http(s) 协议"

        # 主机 SSRF 验证
        host_err = _validate_host(cur_parsed)
        if host_err:
            return f"error: 连接被阻止: {host_err}"

        # 发送请求（无自动重定向）
        req = urllib.request.Request(current_url, method="GET")
        try:
            resp = _no_redirect_opener.open(req, timeout=WEB_FETCH_TIMEOUT)
        except urllib.error.HTTPError as e:
            return f"error: HTTP {e.code} — {current_url}"
        except urllib.error.URLError as e:
            return f"error: 获取失败 {current_url}: {e}"
        except Exception as e:
            return f"error: 获取失败 {current_url}: {e}"

        # 检查是否为重定向
        if resp.status in (301, 302, 303, 307, 308):
            if hops >= WEB_FETCH_MAX_REDIRECTS:
                resp.close()
                return f"error: 重定向次数过多（>{WEB_FETCH_MAX_REDIRECTS}），起始 URL: {url_str}"

            loc = resp.headers.get("Location")
            if not loc:
                # 有重定向状态码但无 Location → 视为终端响应
                response = resp
                break

            # 拒绝非 ASCII Location 头
            try:
                loc.encode("ascii")
            except (UnicodeEncodeError, UnicodeDecodeError):
                resp.close()
                return f"error: 从 {current_url} 重定向时发现非 ASCII Location 头"

            # Location 可能为相对路径 — 基于当前 URL 解析
            new_url = urllib.parse.urljoin(current_url, loc)

            # 逐跳重新验证
            new_parsed = urllib.parse.urlparse(new_url)
            if new_parsed.scheme not in ("http", "https"):
                resp.close()
                return f"error: 禁止的重定向协议 `{new_parsed.scheme}`"
            host_err = _validate_host(new_parsed)
            if host_err:
                resp.close()
                return f"error: 重定向目标被阻止: {host_err}"

            resp.close()
            current_url = new_url
            hops += 1
            continue

        # 非重定向响应
        response = resp
        break

    if response is None:
        return f"error: 重定向次数过多（>{WEB_FETCH_MAX_REDIRECTS}），起始 URL: {url_str}"

    final_url = current_url
    status = response.status

    # 检查 HTTP 错误状态
    if status < 200 or status >= 300:
        response.close()
        return f"error: HTTP {status} — {final_url}"

    # ── Content-Type 检测 ────────────────────────────────────────────────
    ct_header = response.headers.get("Content-Type")
    ct_lower = ct_header.lower() if ct_header else ""
    ct_is_html = "text/html" in ct_lower or "application/xhtml" in ct_lower

    # ── 流式读取（2 MiB 上限） ───────────────────────────────────────────
    body_chunks: List[bytes] = []
    total_bytes = 0
    hit_cap = False
    while True:
        try:
            chunk = response.read(65536)
        except Exception:
            break
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > WEB_FETCH_MAX_BYTES:
            hit_cap = True
            # 只保留上限字节
            excess = total_bytes - WEB_FETCH_MAX_BYTES
            body_chunks.append(chunk[:-excess] if excess < len(chunk) else b"")
            break
        body_chunks.append(chunk)

    response.close()

    raw_body = b"".join(body_chunks)
    if not raw_body:
        return f"error: {final_url} 返回了空响应"

    body = raw_body.decode("utf-8", errors="replace")

    # 形状嗅探回退：仅在服务端未发送 Content-Type 时使用
    # 避免将 JSON 负载（可能以 '<' 开头）错误分类为 HTML
    is_html = ct_is_html or (ct_header is None and body.strip().startswith('<'))

    # ── 格式渲染 ─────────────────────────────────────────────────────────
    output = _render_body(fmt, is_html, body)
    output = _apply_char_cap(output, max_chars)

    cap_note = ""
    if hit_cap:
        total_mib = total_bytes / (1024 * 1024)
        cap_note = f"\n\n[已达到 2 MiB 字节读取上限，实际大小为 {total_mib:.1f} MiB]"

    return f"来自 {final_url} 的内容:\n\n{output}{cap_note}"
