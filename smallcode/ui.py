"""
UI 模块 — smallcode 的交互式 REPL 主循环。

本模块负责：
1. 提供终端中的交互式对话界面（Read-Eval-Print Loop）
2. 显示 AI 助手的文本回复（支持基础 Markdown 渲染）
3. 调用工具并显示实时执行结果
4. 管理对话历史消息列表

``main()`` 是项目的入口函数，通过 ``python -m smallcode`` 启动。
"""

import os
import re
import sys
from typing import Any, Dict, List

from smallcode.api import call_api
from smallcode.config import BOLD, BLUE, CYAN, DIM, GREEN, MODEL, RED, RESET
from smallcode.tools import run_tool


def separator() -> str:
    """
    返回终端宽度的分隔线字符串。

    用于在 REPL 中分隔不同的对话轮次，提升可读性。
    最大宽度限制为 80 列。若无法获取终端大小（如管道模式），
    使用默认宽度 80 列。

    返回:
        格式化的分隔线，如 ``"────────────────────────────────────────"``。
    """
    try:
        cols = os.get_terminal_size().columns
    except OSError:
        cols = 80
    return f"{DIM}{'─' * min(cols, 80)}{RESET}"


def render_markdown(text: str) -> str:
    """
    简单渲染 Markdown 粗体语法为终端粗体转义序列。

    将 ``**文本**`` 替换为 ``\\033[1m文本\\033[0m`` 以便在终端中突出显示。
    其他 Markdown 语法原样输出。

    参数:
        text: 包含 ``**...**`` 语法的文本。

    返回:
        包含 ANSI 转义序列的终端渲染文本。
    """
    return re.sub(r"\*\*(.+?)\*\*", f"{BOLD}\\1{RESET}", text)


def main() -> None:
    """
    启动 smallcode 交互式 REPL 主循环。

    工作流程：
    1. 打印欢迎信息（模型名称、当前工作目录）
    2. 进入无限循环：
       a. 显示输入提示符（蓝色 ``❯``）
       b. 读取用户输入
       c. 特殊命令：``/q`` 或 ``exit`` 退出；``/c`` 清除对话历史
       d. 将用户消息追加到历史
       e. 进入内部代理循环：
          - 调用 API 获取响应
          - 显示文本回复
          - 执行工具调用并显示结果
          - 将工具结果反馈给 API
          - 若无更多工具调用则退出内部循环
       f. 回到步骤 a
    3. 捕获 ``KeyboardInterrupt`` 和 ``EOFError`` 优雅退出

    返回:
        ``None``（直接调用，非函数式返回）。
    """
    cwd = os.getcwd()
    # 显示启动信息
    print(f"{BOLD}smallcode{RESET} | {DIM}{MODEL}{RESET} | {cwd}\n")

    messages: List[Dict[str, Any]] = []
    system_prompt = f"Concise coding assistant. cwd: {cwd}"

    # REPL 主循环
    while True:
        try:
            print(separator())
            user_input = input(f"{BOLD}{BLUE}>{RESET} ").strip()
            print(separator())

            # 处理特殊命令
            if not user_input:
                continue
            if user_input in ("/q", "exit"):
                break
            if user_input == "/c":
                messages = []
                print(f"{GREEN}>> 对话历史已清除{RESET}")
                continue

            # 添加用户消息到历史
            messages.append({"role": "user", "content": user_input})

            # ── 代理内循环：持续调用 API 直到无更多工具调用 ──────────────
            while True:
                response = call_api(messages, system_prompt)
                content_blocks = response.get("content", [])
                tool_results: List[Dict[str, Any]] = []

                for block in content_blocks:
                    # 处理文本回复
                    if block["type"] == "text":
                        print(f"\n{CYAN}>>{RESET} {render_markdown(block['text'])}")

                    # 处理工具调用
                    if block["type"] == "tool_use":
                        tool_name = block["name"]
                        tool_args = block["input"]
                        arg_preview = str(list(tool_args.values())[0])[:50]
                        print(
                            f"\n{GREEN}>> {tool_name.capitalize()}{RESET}({DIM}{arg_preview}{RESET})"
                        )

                        # 执行工具并显示预览
                        result = run_tool(tool_name, tool_args)
                        result_lines = result.split("\n")
                        preview = result_lines[0][:60]
                        if len(result_lines) > 1:
                            preview += f" ... +{len(result_lines) - 1} 行"
                        elif len(result_lines[0]) > 60:
                            preview += "..."
                        print(f"  {DIM}⎿  {preview}{RESET}")

                        # 收集工具结果用于下一轮 API 调用
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block["id"],
                                "content": result,
                            }
                        )

                # 将 AI 的回复（含文本和工具调用）追加到消息历史
                messages.append({"role": "assistant", "content": content_blocks})

                # 若本轮无工具调用，对话轮次结束
                if not tool_results:
                    break

                # 将工具执行结果反馈给 API
                messages.append({"role": "user", "content": tool_results})

            print()

        except (KeyboardInterrupt, EOFError):
            # Ctrl+C 或 Ctrl+D 优雅退出
            break
        except Exception as err:
            print(f"{RED}>> 错误: {err}{RESET}")


if __name__ == "__main__":
    main()
