"""
文件操作工具模块 — 实现 AI 代理对本地文件系统的读写、编辑、搜索能力。

本模块包含六组工具函数，所有函数均接收一个 ``args`` 字典参数，
与 Tool Use 协议的输入格式保持一致：

- ``read(path, offset?, limit?)`` — 读取文件并显示行号
- ``write(path, content)`` — 写入文件（覆盖）
- ``edit(path, old, new, all?)`` — 查找替换文件内容
- ``glob(pat, path?)`` — 按通配符搜索文件（按修改时间排序）
- ``grep(pat, path?, context?, max_results?)`` — 在文件中搜索文本模式
- ``bash(cmd)`` — 执行 Shell 命令并返回输出
"""

import glob as globlib
import os
import re
import subprocess
from typing import Any, Dict, List, Optional

from smallcode.config import BINARY_EXTENSIONS, DIM, RESET, TEXT_EXTENSIONS
from smallcode.encoding import (
    decode_subprocess_output,
    is_binary_extension,
    is_binary_sample,
    try_decode_text,
)


# ── 文件读取 ───────────────────────────────────────────────────────────────────


def read(args: Dict[str, Any]) -> str:
    """
    读取文件并以带行号的格式返回内容。

    支持参数：
    - ``path``（必需）: 文件路径
    - ``offset``（可选，默认 0）: 起始行号（0 索引）
    - ``limit``（可选，默认全部）: 最大返回行数

    自动跳过二进制文件并给出提示。对于无法解码的文本文件，
    会提供 ``chardet`` / ``iconv`` 等进一步诊断建议。

    返回:
        格式化后的文件内容字符串，或错误/提示信息。
    """
    path = args["path"]
    offset = args.get("offset", 0)

    # 扩展名快速判断
    if is_binary_extension(path):
        return f"二进制文件 ({os.path.getsize(path)} 字节)，无法以文本形式显示。"

    # 读取原始字节
    with open(path, "rb") as f:
        raw = f.read()

    # 采样嗅探
    if is_binary_sample(raw[:8192]):
        return f"二进制文件 ({len(raw)} 字节)，无法以文本形式显示。"

    # 尝试解码
    content = try_decode_text(raw)
    if content is None:
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        hint = ""
        if ext in TEXT_EXTENSIONS or not ext:
            hint = (
                "\n\n[提示] 文件无法以 UTF-8/locale 编码解码。可尝试：\n"
                "  python -c \"import chardet; print(chardet.detect(open('{q}','rb').read()))\"\n"
                "  iconv -f 检测到的编码 -t UTF-8 {q}"
            ).format(q=path)
        return f"二进制文件 ({len(raw)} 字节)，无法以文本形式显示。{hint}"

    # 按行分割并根据 offset/limit 截取
    lines = content.splitlines(keepends=True)
    limit = args.get("limit", len(lines))
    selected = lines[offset: offset + limit]
    return "".join(f"{offset + idx + 1:4}| {line}" for idx, line in enumerate(selected))


# ── 文件写入 ───────────────────────────────────────────────────────────────────


def write(args: Dict[str, Any]) -> str:
    """
    将内容写入文件（UTF-8 编码，覆盖模式）。

    支持参数：
    - ``path``（必需）: 文件路径
    - ``content``（必需）: 要写入的文本内容

    返回:
        成功时返回 ``"ok"``。
    """
    with open(args["path"], "w", encoding="utf-8") as f:
        f.write(args["content"])
    return "ok"


# ── 文件编辑 ───────────────────────────────────────────────────────────────────


def edit(args: Dict[str, Any]) -> str:
    """
    在文件中查找并替换文本。

    支持参数：
    - ``path``（必需）: 文件路径
    - ``old``（必需）: 要被替换的旧文本
    - ``new``（必需）: 替换后的新文本
    - ``all``（可选，默认 False）: 是否替换所有匹配项

    编码检测逻辑：
    - 检测 BOM 以确定 UTF-8-SIG / UTF-16
    - 否则尝试 UTF-8 严格解码
    - 回退到系统 locale 编码

    返回:
        成功时返回 ``"ok"``，失败时返回以 ``"error: "`` 开头的错误描述。
    """
    path = args["path"]
    old, new = args["old"], args["new"]

    # 读取原始字节
    with open(path, "rb") as f:
        raw = f.read()

    # 解码
    content = try_decode_text(raw)
    if content is None:
        return "error: 文件无法以文本格式解码"

    # 检测文件原始编码以正确回写
    if raw.startswith(b"\xef\xbb\xbf"):
        file_encoding = "utf-8-sig"
    elif raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        file_encoding = "utf-16"
    else:
        try:
            raw.decode("utf-8")
            file_encoding = "utf-8"
        except UnicodeDecodeError:
            file_encoding = locale.getpreferredencoding()

    # 检查旧文本是否存在
    if old not in content:
        return "error: 未在文件中找到指定的旧文本 (old_string)"
    count = content.count(old)
    if not args.get("all") and count > 1:
        return f"error: 旧文本出现 {count} 次，请指定 ``all=true`` 或让匹配文本唯一"

    # 执行替换
    replacement = (
        content.replace(old, new) if args.get("all") else content.replace(old, new, 1)
    )
    with open(path, "w", encoding=file_encoding) as f:
        f.write(replacement)
    return "ok"


# 延迟导入（仅在 edit 函数中用到系统 locale 编码时引入）
import locale


# ── 文件通配搜索 ──────────────────────────────────────────────────────────────


def glob(args: Dict[str, Any]) -> str:
    """
    按通配符模式搜索文件，结果按修改时间降序排列。

    支持参数：
    - ``pat``（必需）: 通配符模式（如 ``**/*.py``）
    - ``path``（可选，默认 ``"."``）: 搜索根目录

    返回:
        匹配文件的路径列表（每行一个），若无匹配返回空字符串。
    """
    pattern = (args.get("path", ".") + "/" + args["pat"]).replace("//", "/")
    files = globlib.glob(pattern, recursive=True)
    files = sorted(
        files,
        key=lambda f: os.path.getmtime(f) if os.path.isfile(f) else 0,
        reverse=True,
    )
    return "\n".join(files)


# ── 文件内容搜索 ──────────────────────────────────────────────────────────────


def grep(args: Dict[str, Any]) -> str:
    """
    在文件中搜索文本模式并返回匹配行（含上下文）。

    支持参数：
    - ``pat``（必需）: 搜索模式（正则表达式，大小写自动感知）
    - ``path``（可选，默认 ``"."``）: 搜索根目录
    - ``context``（可选，默认 0，最大 10）: 匹配行上下文件行数
    - ``max_results``（可选，默认 50）: 最大结果数量

    智能大小写：若模式中不含大写字母则自动忽略大小写。
    正则失败时自动降级为字面量搜索。

    自动跳过：
    - 二进制文件（扩展名 + 内容嗅探）
    - ``node_modules``、``target``、``dist``、``datalog``、``.git`` 目录
    - ``.log`` 文件

    返回:
        格式化后的匹配结果字符串，包含文件名、行号、行内容。
    """
    # 智能大小写：模式含大写字母则区分大小写
    has_upper = any(c.isupper() for c in args["pat"])
    try:
        pattern = re.compile(args["pat"], 0 if has_upper else re.IGNORECASE)
    except re.error:
        # 正则编译失败 → 转义后作为字面量搜索
        pattern = re.compile(re.escape(args["pat"]), 0 if has_upper else re.IGNORECASE)

    context = min(args.get("context", 0), 10)
    max_results = args.get("max_results", 50)
    hits: List[str] = []
    files_searched = 0

    # 噪音目录：搜索时跳过这些目录以减少无关结果
    noise_dirs = frozenset({"node_modules", "target", "dist", "datalog", ".git"})
    noise_exts = frozenset({".log"})

    # 递归遍历所有文件
    for filepath in globlib.glob(args.get("path", ".") + "/**", recursive=True):
        if not os.path.isfile(filepath):
            continue
        # 跳过噪音目录
        parts = filepath.replace("\\", "/").split("/")
        if any(p in noise_dirs for p in parts):
            continue
        # 跳过噪音扩展名
        if os.path.splitext(filepath)[1].lower() in noise_exts:
            continue

        try:
            with open(filepath, "rb") as f:
                raw = f.read()
            # 跳过二进制文件
            if is_binary_extension(filepath) or is_binary_sample(raw[:8192]):
                continue
            text = try_decode_text(raw)
            if text is None:
                continue

            lines = text.splitlines()
            match_indices = [i for i, line in enumerate(lines) if pattern.search(line)]
            if not match_indices:
                continue

            files_searched += 1
            for mi in match_indices:
                if len(hits) >= max_results:
                    break
                start = max(0, mi - context)
                end = min(len(lines), mi + context + 1)
                for i in range(start, end):
                    prefix = ":" if i == mi else "-"
                    hits.append(f"{filepath}:{i + 1}{prefix} {lines[i]}")
            if len(hits) >= max_results:
                break
        except Exception:
            # 跳过无法读取的文件（权限、编码等）
            pass

    # 格式化输出
    if not hits:
        output = f"未找到匹配 '{args['pat']}'"
        if files_searched:
            output += f"（已搜索 {files_searched} 个文件）"
        return output

    output = "\n".join(hits)
    stats = f"（已搜索 {files_searched} 个文件）"
    if len(hits) >= max_results:
        output += f"\n\n[结果已截断至 {max_results} 条]{stats}"
    else:
        output += stats
    return output


# ── Shell 命令执行 ────────────────────────────────────────────────────────────


def bash(args: Dict[str, Any]) -> str:
    """
    执行 Shell 命令并实时打印输出，同时返回完整结果。

    支持参数：
    - ``cmd``（必需）: 要执行的 Shell 命令字符串

    命令通过 ``shell=True`` 执行，支持管道、重定向等 Shell 特性。
    输出通过 ``decode_subprocess_output`` 进行跨平台编码兼容处理。
    超时限制为 30 秒，超时后进程将被强制终止。

    返回:
        命令的标准输出（含标准错误），若为空则返回 ``"(empty)"``。
    """
    proc = subprocess.Popen(
        args["cmd"],
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output_chunks: List[bytes] = []

    try:
        # 逐行读取以实现实时打印
        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                decoded_line = decode_subprocess_output(line.rstrip(b"\r\n"))
                print(f"  {DIM}│ {decoded_line}{RESET}", flush=True)
                output_chunks.append(line)
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        output_chunks.append(b"\n(timed out after 30s)")

    full_output = decode_subprocess_output(b"".join(output_chunks))
    return full_output.strip() or "(empty)"
