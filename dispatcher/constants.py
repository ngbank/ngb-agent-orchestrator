"""Lightweight display constants shared across dispatcher command/UI modules."""

# Status display config: (emoji, label)
STATUS_DISPLAY = {
    "pending": ("🕐", "pending"),
    "in_progress": ("⚙️ ", "in_progress"),
    "pending_workplan_clarification": ("💬", "pending_workplan_clarification"),
    "pending_approval": ("⏸️ ", "pending_approval"),
    "pending_pr_approval": ("🔍", "pending_pr_approval"),
    "pr_commented": ("💬", "pr_commented"),
    "approved": ("✅", "approved"),
    "rejected": ("🚫", "rejected"),
    "completed": ("🎉", "completed"),
    "failed": ("❌", "failed"),
    "cancelled": ("⛔", "cancelled"),
}

# Node display config: emoji per top-level node name
NODE_EMOJI = {
    "__start__": "▶ ",
    "work_planner": "📋",
    "await_approval": "⏸️ ",
    "execute_plan": "⚙️ ",
    "__end__": "🏁",
}
