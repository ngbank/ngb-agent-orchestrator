"""Node: cleanup — delete temp files and the cloned working directory.

This node always runs, regardless of success or failure, ensuring no temp
directories are leaked.
"""

from orchestrator.shared.repo_setup.nodes import build_cleanup_node

cleanup = build_cleanup_node(
    temp_file_keys=(
        "work_plan_path",
        "summary_path",
        "reasoning_path",
        "pr_comments_path",
    )
)
