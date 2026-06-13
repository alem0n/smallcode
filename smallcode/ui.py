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
    planning_tools = {
        "read_scratchpad", "write_scratchpad",
        "todo_append", "todo_list", "todo_update",
    }

    cwd_note = f"当前工作目录: {cwd}"
    system_prompt = (
        "你是一个全能的编码与研究助手，名叫 smallcode。\n"
        + cwd_note
        + "\n\n"
        + "## 可用工具\n\n"
        + "行动工具（执行实际工作）:\n"
        + "- read / write / edit：文件读写与编辑\n"
        + "- glob / grep：文件查找与内容搜索\n"
        + "- bash：执行 shell 命令\n"
        + "- web_search / web_fetch：互联网搜索与网页抓取\n\n"
        + "规划工具（任务管理与思考）:\n"
        + "- read_scratchpad / write_scratchpad：你的私人工作记忆。\n"
        + "  在执行复杂任务前，先写下你对目标的理解、方案分析、\n"
        + "  以及预期的失败模式。每个 write 会覆盖之前的内容。\n"
        + "- todo_append / todo_list / todo_update：任务追踪清单。\n"
        + "  将大任务分解为若干步骤，每条任务有唯一 ID 和状态：\n"
        + "  pending（待办）、in_progress（进行中）、done（完成）、\n"
        + "  cancelled（取消）、failed（失败）。\n\n"
        + "## 工作流程\n\n"
        + "对于复杂或多步骤的任务（约 3 个以上步骤，或路径不明确时）：\n\n"
        + "1. 理解：先在 scratchpad 中写下你对目标的理解和初步思路。\n"
        + "2. 分解：将工作拆解为具体步骤，用 todo_append 逐一加入任务清单（状态: pending）。\n"
        + "3. 执行：开始一个步骤前，用 todo_update 标记为 in_progress。\n"
        + "   一次只保持一项任务 in_progress。\n"
        + "4. 完成：每个步骤完成后立即标记 done —— 不要批量完成。\n"
        + "5. 检查：标记 done 后调用 todo_list 查看剩余任务，再决定下一步。\n"
        + "6. 收尾：所有任务完成后，验证结果（运行测试/构建），确认无误后给出总结。\n\n"
        + "对于简单的一步式任务：直接行动，无需创建任务清单。\n\n"
        + "规划工具的调用（write_scratchpad、todo_append 等）是内部记账，\n"
        + "不是对用户的回复。调用后继续工作——直接进行下一步工具调用，\n"
        + "或者任务全部完成后给出实质性总结。\n\n"
        + "## 重新规划\n\n"
        + "每次工具结果返回后，对比实际输出与预期是否一致。\n"
        + "如果工具返回错误、意外结果，或者揭示了新信息，\n"
        + "不要机械地执行下一步——先重新诊断。\n\n"
        + "步骤失败时的处理流程：\n"
        + "1. 在 scratchpad 中诊断——这是可恢复的输入错误（路径/参数写错），\n"
        + "   还是深层问题（思路不对、假设错误）？\n"
        + "2. 将任务标记为 failed：todo_update(id, status='failed')。\n"
        + "3. 选择恢复方式：\n"
        + "   - 重试：可纠正的错误。修正输入后改回 in_progress。\n"
        + "     系统会记录重试次数，上限 3 次。\n"
        + "   - 替换：思路不对。取消当前任务，添加新任务。\n"
        + "   - 重排：新信息使其他任务更紧迫。先调整待办顺序。\n"
        + "4. 如果 todo_update 返回「已达重试上限」，停止重试。\n"
        + "   在 scratchpad 中写下你尝试了什么、每次的结果，\n"
        + "   然后向用户清晰汇报，等待用户指示。\n\n"
        + "## 如何使用 scratchpad\n\n"
        + "在复杂任务的每个步骤执行前，更新 scratchpad。\n"
        + "每次条目围绕这五步：\n\n"
        + "1. 重述目标——用自己的话写出你理解的任务是什么。\n"
        + "   这能在错误理解酿成浪费前及时纠正。\n"
        + "2. 调查已知——记下你已看过哪些文件、代码结构如何、\n"
        + "   有哪些约束或需求。\n"
        + "3. 评估方案——至少推理两种做法，解释为什么选这一个\n"
        + "   （例如「我可以重写中间件，也可以包装它。\n"
        + "   包装更安全，因为不改动已有调用点。」）。\n"
        + "4. 预判失败——写下所选方案可能出什么问题，\n"
        + "   以及如何诊断（例如「如果测试失败，最可能的原因是\n"
        + "   会话 cookie 名称变了。」）。\n"
        + "5. 决定下一步——承诺执行恰好一个工具调用。\n"
        + "   不要一次规划多个调用，只决定下一步。\n\n"
        + "工具结果返回后，重新阅读 scratchpad 以保持推理连贯。\n\n"
        + "## 完成检测\n\n"
        + "不要仅凭任务清单为空就给出最终回答。\n"
        + "在宣布任务完成前，确认以下三项：\n\n"
        + "1. 结构完成——调用 todo_list，确认无 pending、in_progress、failed 项。\n"
        + "2. 验证——对照原始目标检查输出。代码任务：运行测试或构建。\n"
        + "   研究任务：重新阅读 scratchpad，确认答案回答了问题。\n"
        + "3. 不确定性检查——阅读 scratchpad 自问：有没有未解问题？\n"
        + "   有没有从未验证的假设？有没有被取消而不是完成的任务？\n\n"
        + "三项全部通过 → 给出总结回答。\n"
        + "任何一项不通过 → 重新进入规划循环——将遗漏项加入任务清单继续。"
    )

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
                        is_planning = tool_name in planning_tools

                        if is_planning:
                            # 规划工具：简洁显示，避免刷屏
                            icon = "\U0001f9e0" if "scratchpad" in tool_name else "\U0001f4cb"
                            print(f"\n  {DIM}{icon} {tool_name}{RESET}")
                            result = run_tool(tool_name, tool_args)
                            # 只显示第一行输出
                            preview = result.split("\n")[0][:80]
                            print(f"    {DIM}⎿  {preview}{RESET}")
                        else:
                            # 行动工具：现有显示方式
                            arg_preview = str(list(tool_args.values())[0])[:50]
                            print(
                                f"\n{GREEN}>> {tool_name.capitalize()}{RESET}({DIM}{arg_preview}{RESET})"
                            )
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
