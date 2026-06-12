# smallcode

命令行 AI 编码助手 — 本地文件操作、网页搜索与互联网搜索，
以及基于 DeepSeek API 的智能对话能力。

## 功能

- **文件操作**：读取、写入、编辑（查找替换）、通配搜索、内容搜索
- **命令执行**：在本地 Shell 中运行命令并获取输出
- **网页搜索**：通过 DuckDuckGo 搜索互联网
- **网页抓取**：安全抓取网页（含 SSRF 防护），HTML 自动转 Markdown
- **AI 对话**：基于 DeepSeek API 的智能编码助手

## 快速开始

```bash
# 1. 设置 API Key
set DEEPSEEK_API_KEY=sk-your-key-here

# 2. 启动 REPL
python -m smallcode
```

## 目录结构

```
smallcode/
├── __init__.py        # 包入口，导出 main()
├── __main__.py        # python -m smallcode 启动点
├── config.py          # 全局配置（API、颜色、扩展集合）
├── encoding.py        # 编码检测与文本解码工具
├── api.py             # DeepSeek API 客户端
├── ui.py              # REPL 主循环与终端渲染
└── tools/
    ├── __init__.py    # 工具注册表 + run_tool + make_schema
    ├── file_tools.py  # 文件读写、搜索、命令执行
    ├── web_search.py  # DuckDuckGo 搜索
    └── web_fetch.py   # SSRF 防护 + HTTP 抓取 + HTML 转换
```

## 命令参考

| 命令 | 作用 |
|------|------|
| `/q` 或 `exit` | 退出程序 |
| `/c` | 清除对话历史 |
| `Ctrl+C` / `Ctrl+D` | 中断或退出 |

## 相关项目

smallcode 受以下项目启发，但在功能定位上各有侧重：

- **[Claude Code](https://github.com/anthropics/claude-code)** — Anthropic 官方推出的 AI 编码代理，功能完整，依赖 Anthropic API。
- **[AtomCode](https://atomgit.com)** — AtomGit 平台的 AI 编码代理，提供多种模型支持，集成于 AtomGit 生态。
- **[NanoCode](https://github.com/1rgs/nanocode)** — 极简 AI 编码代理，追求最小体积和零依赖，以单文件形式发布。

smallcode 定位介于它们之间：保持轻量但提供完整的工具集（文件操作、网页搜索、抓取），
通过 DeepSeek API 驱动，专注于个人开发者的日常编码辅助需求。
