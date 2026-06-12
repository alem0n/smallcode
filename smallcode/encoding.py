"""
编码检测与文本解码模块。

本模块提供一组工具函数，用于判断文件是否为二进制，
以及尝试使用多种编码策略将字节数据解码为文本。

解码策略（分层降级）：
  1. BOM 检测（UTF-8-SIG、UTF-16）
  2. 严格 UTF-8 解码
  3. 系统 locale 编码（``locale.getpreferredencoding()``）
  4. 子进程输出专用：Windows OEM 代码页降级
  5. 最终回退：UTF-8 替换模式
"""

import locale
import os
import sys
from typing import Optional

from smallcode.config import BINARY_EXTENSIONS, TEXT_EXTENSIONS


def is_binary_extension(path: str) -> bool:
    """
    通过文件扩展名判断是否为二进制文件。

    查询 ``BINARY_EXTENSIONS`` 集合；若扩展名在其中，返回 ``True``。

    参数:
        path: 文件路径字符串。

    返回:
        扩展名为已知二进制类型时返回 ``True``，否则 ``False``。
    """
    _, ext = os.path.splitext(path)
    return ext.lower().lstrip(".") in BINARY_EXTENSIONS


def is_binary_sample(sample: bytes) -> bool:
    """
    通过采样内容嗅探文件是否为二进制。

    检测规则：
    - 若前 8192 字节中出现 NUL 字节（\\x00）→ 二进制
    - 若不可打印字符占比超过 30% → 二进制

    参数:
        sample: 文件头部的字节采样（通常为 8192 字节）。

    返回:
        判定为二进制时返回 ``True``。
    """
    if not sample:
        return False
    # NUL 字节是强二进制信号
    for b in sample:
        if b == 0:
            return True
    # 统计不可打印字符（排除 \\t、\\n、\\r 等常见控制字符）
    non_printable = sum(
        1 for b in sample
        if b < 9 or b == 11 or (b > 13 and b < 32)
    )
    return non_printable * 10 > len(sample) * 3


def try_decode_text(raw: bytes) -> Optional[str]:
    """
    使用分层策略尝试将字节数据解码为文本。

    策略顺序：
    1. BOM 引导编码（UTF-8-SIG → UTF-16）
    2. 严格 UTF-8
    3. ``locale.getpreferredencoding()``（通常是 Windows GBK / Linux UTF-8）

    参数:
        raw: 待解码的字节数据。

    返回:
        解码成功时返回字符串；所有解码尝试均失败时返回 ``None``。
    """
    # ── Phase 1: BOM 检测 ─────────────────────────────────────────────────
    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass

    # ── Phase 2: 严格 UTF-8 ──────────────────────────────────────────────
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    # ── Phase 3: 系统 locale 编码 ─────────────────────────────────────────
    try:
        enc = locale.getpreferredencoding()
        if enc and enc.lower() not in ("utf-8", "utf8"):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                pass
    except Exception:
        pass

    return None


def decode_subprocess_output(data: bytes) -> str:
    """
    解码子进程的标准输出/标准错误。

    子进程输出比普通文件更复杂，因为 Windows 的 cmd.exe 可能使用
    与控制台不同的代码页。解码策略：
    1. 严格 UTF-8（现代工具如 git、cargo、npm 的输出）
    2. Windows 专有：调用 ``GetOEMCP()`` 获取 OEM 代码页
       - 当 ``cp=65001``（UTF-8 模式）时，cmd.exe 的资源字符串仍然使用
         原始 OEM 代码页（如 936/GBK），因此额外尝试常见东亚编码
    3. 最终回退：UTF-8 替换模式

    参数:
        data: 子进程输出的原始字节数据。

    返回:
        解码后的字符串（保证总有返回值）。
    """
    # ── Phase 1: 严格 UTF-8 ──────────────────────────────────────────────
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass

    # ── Phase 2: Windows OEM 代码页 ──────────────────────────────────────
    if sys.platform == "win32":
        try:
            import ctypes
            oem_cp = ctypes.windll.kernel32.GetOEMCP()
            try:
                return data.decode(f"cp{oem_cp}")
            except (UnicodeDecodeError, LookupError):
                pass
            # UTF-8 模式下的降级：尝试常见东亚编码
            if oem_cp == 65001:
                for fallback_cp in (936, 950, 932, 949):
                    try:
                        result = data.decode(f"cp{fallback_cp}")
                        if result.count("\ufffd") < len(result) // 2:
                            return result
                    except (UnicodeDecodeError, LookupError):
                        continue
        except Exception:
            pass

    # ── Phase 3: 最终回退 ────────────────────────────────────────────────
    return data.decode("utf-8", errors="replace")
