"""
长任务规划工具 — Scratchpad（草稿本）和 To-Do List（任务清单）。

本模块为 AI 代理提供两种"内部思考"工具：
1. Scratchpad：进程内存中的读写空间，让模型写下思考过程、方案分析、中间结论。
2. To-Do List：任务追踪表，支持 pending / in_progress / done / cancelled / failed 状态，
   带有重试计数和重试上限，防止无限循环。

两个工具均保存在进程内存中，不跨会话共享，简单可靠。
"""

from typing import Any, Dict, List, Optional

RETRY_LIMIT = 3


# ── Scratchpad ──────────────────────────────────────────────────────────────


class Scratchpad:
    """进程内存中的草稿本，每次 write 覆盖前次内容。"""

    def __init__(self) -> None:
        self._content: str = ""

    def read(self) -> str:
        """读取草稿本内容，空时返回 '(empty)'。"""
        return self._content if self._content else "(empty)"

    def write(self, content: str) -> None:
        """写入草稿本（覆盖旧内容）。"""
        self._content = str(content).strip()


_scratchpad = Scratchpad()


def read_scratchpad(args: Dict[str, Any]) -> str:
    """读取草稿本的当前内容。"""
    return _scratchpad.read()


def write_scratchpad(args: Dict[str, Any]) -> str:
    """
    写入草稿本。先前的内容将被完全覆盖。

    参数:
        content: 要写入的文本内容。
    """
    content = args.get("content", "")
    _scratchpad.write(content)
    return "🧠 草稿本已更新"


# ── To-Do List ─────────────────────────────────────────────────────────────


class ToDoList:
    """进程内存中的任务清单，维护任务状态和重试次数。"""

    STATUSES = ["pending", "in_progress", "done", "cancelled", "failed"]

    def __init__(self) -> None:
        self._items: List[Dict[str, Any]] = []

    def read(self, include_completed: bool = False) -> List[Dict[str, Any]]:
        """读取任务清单。默认过滤掉 done 和 cancelled 项。"""
        if include_completed:
            return [item.copy() for item in self._items]
        return [
            item.copy()
            for item in self._items
            if item["status"] not in ("done", "cancelled")
        ]

    def append(self, id: str, content: str, status: str) -> Dict[str, Any]:
        """追加一个新任务。"""
        if status not in self.STATUSES:
            raise ValueError(
                f"无效状态 '{status}'。有效值：{', '.join(self.STATUSES)}"
            )
        if self.contains(id):
            raise ValueError(f"任务 ID '{id}' 已存在！")
        new_item = {
            "id": id,
            "content": str(content),
            "status": status,
            "retries": 0,
        }
        self._items.append(new_item)
        return new_item.copy()

    def contains(self, id: str) -> bool:
        """检查是否已存在指定 ID 的任务。"""
        return any(item["id"] == id for item in self._items)

    def update(
        self, id: str, content: Optional[str] = None, status: Optional[str] = None
    ) -> Dict[str, Any]:
        """更新任务的内容和/或状态。"""
        if content is None and status is None:
            raise ValueError("未提供 content 或 status，无需更新。")

        if status is not None and status not in self.STATUSES:
            raise ValueError(
                f"无效状态 '{status}'。有效值：{', '.join(self.STATUSES)}"
            )

        for item in self._items:
            if item["id"] == id:
                if content is not None:
                    item["content"] = str(content)
                if status is not None:
                    prev = item["status"]
                    item["status"] = status
                    # 如果从 failed 回到 in_progress，计为重试
                    if prev == "failed" and status == "in_progress":
                        item["retries"] += 1
                return item.copy()

        raise ValueError(f"未找到 ID 为 '{id}' 的任务")


_todo = ToDoList()


def todo_append(args: Dict[str, Any]) -> str:
    """
    向任务清单追加一条新任务。

    参数:
        id: 唯一任务标识。
        content: 任务描述。
        status: 初始状态（pending / in_progress / done / cancelled / failed）。
    """
    id_val = str(args.get("id", ""))
    content_val = str(args.get("content", ""))
    status_val = str(args.get("status", "pending"))
    try:
        _todo.append(id_val, content_val, status_val)
        return f"📋 已添加任务 [{id_val}]：{content_val}（{status_val}）"
    except (ValueError, Exception) as e:
        return f"error: 添加任务失败: {e}"


def todo_list(args: Dict[str, Any]) -> str:
    """
    查看任务清单。默认仅显示未完成的任务。

    参数:
        include_completed: （可选）设为 true 则同时显示已完成/已取消的任务。
    """
    include_completed = args.get("include_completed", False)
    items = _todo.read(include_completed=include_completed)

    if not items:
        return "📋 任务清单为空"

    # 按状态分组计数
    counts: Dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1

    lines = [f"📋 任务清单（共 {len(items)} 项）"]
    for s in ToDoList.STATUSES:
        if s in counts:
            lines.append(f"  {counts[s]} 项 {s}")
    lines.append("─" * 30)

    for item in items:
        retry_note = f"（{item['retries']} 次重试）" if item["retries"] > 0 else ""
        lines.append(
            f"  [{item['id']}] {item['content']} — {item['status']}{retry_note}"
        )

    return "\n".join(lines)


def todo_update(args: Dict[str, Any]) -> str:
    """
    更新任务的内容或状态。

    参数:
        id: 任务 ID。
        content: （可选）新的任务描述。
        status: （可选）新的状态值。
    """
    id_val = str(args.get("id", ""))
    content_val = args.get("content")
    status_val = args.get("status")

    if content_val is None and status_val is None:
        return "请至少提供 content 或 status 之一。"

    if status_val is not None:
        status_val = str(status_val)

    try:
        item = _todo.update(id_val, content_val, status_val)
        retries = item["retries"]
        parts = []
        if status_val:
            parts.append(f"状态 → {status_val}")
        if content_val:
            parts.append(f"内容已更新")
        msg = f"📋 任务 [{id_val}]：{'，'.join(parts)}"

        # 重试提醒
        if status_val == "in_progress" and retries > 0:
            if retries >= RETRY_LIMIT:
                msg += (
                    f"\n⚠️  这是第 {retries}/{RETRY_LIMIT} 次重试（已达上限）。"
                    "请停止重试并向用户上报。"
                )
            else:
                msg += f"\n⚠️  第 {retries}/{RETRY_LIMIT} 次重试。"
        return msg
    except (ValueError, Exception) as e:
        return f"error: 更新任务失败: {e}"
