"""Restore the pinned native approval registry from server-owned frozen interrupts."""

import json

from agent_framework_ag_ui import AgentFrameworkAgent
from agent_framework_ag_ui._approval_lifecycle import ApprovalExecutionOwner
from agent_framework_ag_ui._approval_state import approval_state_thread_id


class DurableApprovalAgent(AgentFrameworkAgent):
    def restore_interrupts(self, owner, thread, interrupts):
        # The pinned adapter has no public constructor option for its approval store.
        # Native HTTP resume tests cover this narrow registration hook on every upgrade.
        for interrupt in interrupts:
            call = interrupt["metadata"]["agent_framework"]["function_call"]
            self._approval_state_store.register(
                thread_ids=[approval_state_thread_id(scope=str(owner), thread_id=str(thread))],
                scope=str(owner),
                name=call["name"],
                arguments=json.dumps(call["arguments"], sort_keys=True, separators=(",", ":")),
                request_id=interrupt["id"],
                interrupt_id=interrupt["id"],
                owner=ApprovalExecutionOwner.LOCAL,
                call_id=call["call_id"],
                function_call_id=interrupt["id"],
            )
