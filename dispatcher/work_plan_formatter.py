"""
WorkPlan comment formatter for JIRA.

This module formats WorkPlan JSON into human-readable Jira comments with
required sections for reviewer approval.
"""

from typing import Dict, List


class WorkPlanCommentFormatter:
    """Formats WorkPlan JSON as readable Jira markdown comment."""
    
    def __init__(self):
        """Initialize the formatter."""
        pass
    
    def format(self, work_plan: Dict, ticket_id: str) -> str:
        """
        Format WorkPlan as Jira comment with required sections.
        
        Args:
            work_plan: WorkPlan dictionary containing plan details
            ticket_id: JIRA ticket key (e.g., 'AOS-39')
        
        Returns:
            str: Formatted Jira markdown comment
        
        Required sections in output:
        - Plan Summary (approach and status)
        - Task List
        - Risks
        - Questions for Reviewer
        - Approval Instructions
        """
        sections = []
        
        # Header with version marker
        sections.append("<!-- WorkPlan v1.0 -->")
        sections.append("")
        
        # Title
        sections.append(f"# 🤖 Agent WorkPlan for {ticket_id}")
        sections.append("")
        
        # Summary section
        summary = work_plan.get('summary', 'No summary provided')
        sections.append(f"*{summary}*")
        sections.append("")
        
        # Plan Status indicator
        status = work_plan.get('status', 'unknown')
        status_emoji = {
            'pass': '✅',
            'concerns': '⚠️',
            'blocked': '🚫'
        }
        emoji = status_emoji.get(status, '❓')
        sections.append(f"**Status:** {emoji} {status.upper()}")
        sections.append("")
        
        # Approach section
        sections.append("## 📋 Plan Summary")
        sections.append("")
        approach = work_plan.get('approach', 'No approach specified')
        sections.append(approach)
        sections.append("")
        
        # Task List section
        sections.append("## ✅ Task List")
        sections.append("")
        tasks = work_plan.get('tasks', [])
        if tasks:
            for task in tasks:
                task_id = task.get('id', '?')
                description = task.get('description', 'No description')
                files = task.get('files_likely_affected', [])
                
                sections.append(f"### Task {task_id}")
                sections.append(description)
                if files:
                    sections.append("")
                    sections.append("*Files likely affected:*")
                    for file_path in files:
                        sections.append(f"- {{code}}{file_path}{{code}}")
                sections.append("")
        else:
            sections.append("*No tasks defined*")
            sections.append("")
        
        # Risks section
        sections.append("## ⚠️ Risks")
        sections.append("")
        risks = work_plan.get('risks', [])
        if risks:
            for risk in risks:
                sections.append(f"- {risk}")
            sections.append("")
        else:
            sections.append("*No risks identified*")
            sections.append("")
        
        # Questions for Reviewer section
        sections.append("## ❓ Questions for Reviewer")
        sections.append("")
        questions = work_plan.get('questions_for_reviewer', [])
        if questions:
            for question in questions:
                sections.append(f"- {question}")
            sections.append("")
        else:
            sections.append("*No questions*")
            sections.append("")
        
        # Approval Instructions section
        sections.append("## 🎯 Approval Instructions")
        sections.append("")
        sections.append("To approve this plan and allow the agent to proceed:")
        sections.append("{code}")
        sections.append(f"dispatcher approve {ticket_id}")
        sections.append("{code}")
        sections.append("")
        sections.append("To reject this plan and halt execution:")
        sections.append("{code}")
        sections.append(f"dispatcher reject {ticket_id}")
        sections.append("{code}")
        sections.append("")
        
        # Footer with metadata
        sections.append("---")
        sections.append(f"*WorkPlan Schema Version: {work_plan.get('schema_version', 'unknown')}*")
        
        return "\n".join(sections)


def format_work_plan_comment(work_plan: Dict, ticket_id: str) -> str:
    """
    Convenience function to format a WorkPlan as a Jira comment.
    
    Args:
        work_plan: WorkPlan dictionary
        ticket_id: JIRA ticket key
    
    Returns:
        str: Formatted Jira markdown comment
    
    Example:
        >>> work_plan = {
        ...     'schema_version': '1.0',
        ...     'ticket_key': 'AOS-39',
        ...     'summary': 'Implement comment posting',
        ...     'approach': 'Add ACLI integration',
        ...     'tasks': [{'id': 1, 'description': 'Create formatter', 'files_likely_affected': ['formatter.py']}],
        ...     'risks': ['ACLI might not be installed'],
        ...     'questions_for_reviewer': ['Should we handle large comments?'],
        ...     'status': 'pass'
        ... }
        >>> comment = format_work_plan_comment(work_plan, 'AOS-39')
        >>> print(comment)
    """
    formatter = WorkPlanCommentFormatter()
    return formatter.format(work_plan, ticket_id)
