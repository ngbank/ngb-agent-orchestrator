"""Outbound event dataclass for the execution-events Service Bus topic."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ExecutionStatusEvent:
    """Wire-format message consumed by FleetOps ExecutionStatusEvent DTO.

    All field names map to the camelCase JSON keys expected by the consumer.
    """

    execution_id: str
    event_type: str
    orchestrator_workflow_id: str
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4()}")
    status: Optional[str] = None
    pr_url: Optional[str] = None
    error_message: Optional[str] = None
    ticket_id: Optional[str] = None
    work_plan: Optional[Any] = None

    def to_json(self) -> str:
        """Serialise to camelCase JSON string (Jackson-compatible)."""
        payload: dict[str, Any] = {
            "eventId": self.event_id,
            "executionId": self.execution_id,
            "eventType": self.event_type,
            "orchestratorWorkflowId": self.orchestrator_workflow_id,
        }
        if self.status is not None:
            payload["status"] = self.status
        if self.pr_url:
            payload["prUrl"] = self.pr_url
        if self.error_message:
            payload["errorMessage"] = self.error_message
        if self.ticket_id:
            payload["ticketId"] = self.ticket_id
        if self.work_plan is not None:
            payload["workPlan"] = self.work_plan
        return json.dumps(payload)
