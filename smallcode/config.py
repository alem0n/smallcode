"""
配置模块 — smallcode 全局常量与运行配置。

本模块是整个项目的配置中心，存放所有不依赖外部模块的常量定义，
包括 API 端点、模型名称、ANSI 终端颜色、以及二进制/文本文件扩展集合。
其他模块通过 ``from smallcode.config import ...`` 引入所需常量。
"""

import os

# ── API 配置 ──────────────────────────────────────────────────────────────────
# 优先从环境变量读取 API Key，避免硬编码密钥泄露风险
DEEPSEEK_API_KEY: str = os.environ.get("DEEPSEEK_API_KEY", "")

# API 基础地址与模型标识
API_URL: str = "https://api.deepseek.com/anthropic/v1/messages"
MODEL: str = "deepseek-v4-flash"

# 若未设置 API Key，在启动时给出提示（但仍然允许导入，延迟到调用时检查）
if not DEEPSEEK_API_KEY:
    import sys
    print(
        "[smallcode] 警告: 环境变量 DEEPSEEK_API_KEY 未设置。\n"
        "           请通过 `set DEEPSEEK_API_KEY=sk-xxx` 配置 API Key。",
        file=sys.stderr,
    )

# ── ANSI 终端颜色 ────────────────────────────────────────────────────────────
# 用于 REPL 界面中的文字着色，提升可读性
RESET: str = "\033[0m"      # 重置所有样式
BOLD: str = "\033[1m"       # 粗体
DIM: str = "\033[2m"        # 暗淡（用于次要信息）
BLUE: str = "\033[34m"      # 蓝色（用户输入提示符）
CYAN: str = "\033[36m"      # 青色（AI 回复前缀）
GREEN: str = "\033[32m"     # 绿色（工具调用前缀）
YELLOW: str = "\033[33m"    # 黄色（预留）
RED: str = "\033[31m"       # 红色（错误信息）

# ── 二进制扩展名集合 ─────────────────────────────────────────────────────────
# 已知的二进制文件扩展名，用于 read/grep 工具跳过非文本文件
BINARY_EXTENSIONS: frozenset = frozenset({
    # 图片
    "png", "jpg", "jpeg", "gif", "bmp", "ico",
    # 压缩包
    "zip", "tar", "gz", "bz2", "xz", "zst", "7z", "rar",
    # 文档（Office / PDF）
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
    # 可执行文件与库
    "exe", "dll", "so", "dylib", "bin", "dat", "lib",
    # 磁盘镜像
    "iso", "img", "dmg",
    # 数据库
    "db", "sqlite", "sqlite3",
    # 音视频
    "mp3", "mp4", "avi", "mkv", "mov", "wav", "flac",
    # 字体
    "ttf", "otf", "woff", "woff2",
    # Python 字节码
    "pyc", "pyo",
})

# ── 文本扩展名集合 ───────────────────────────────────────────────────────────
# 已知的文本文件扩展名，用于 read 工具提供编码错误时的提示信息
TEXT_EXTENSIONS: frozenset = frozenset({
    # 纯文本 / 标记 / 数据
    "txt", "md", "markdown", "csv", "tsv", "log", "sql",
    # 配置文件
    "ini", "conf", "cfg", "toml", "yaml", "yml", "json", "xml",
    "cfg", "env", "ron",
    # 编程语言
    "py", "rs", "js", "ts", "jsx", "tsx", "go", "java",
    "c", "cpp", "h", "hpp", "css", "scss", "less",
    "html", "htm", "vue", "svelte",
    # 脚本
    "sh", "bash", "zsh", "fish", "ps1", "bat", "cmd",
    # 其他语言
    "r", "rb", "php", "swift", "kt", "scala", "lua", "pl",
    "hs", "ex", "exs",
    # 构建配置
    "cmake", "makefile", "dockerfile",
    # VCS / 编辑器
    "gitignore", "gitattributes", "editorconfig",
})
