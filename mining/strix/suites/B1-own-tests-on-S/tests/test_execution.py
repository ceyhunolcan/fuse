from __future__ import annotations
import asyncio
import pytest
from strix.core.agents import AgentCoordinator

@pytest.mark.asyncio
async def test_nudge_names_the_root_finish_tool() -> None:
    from strix.core.execution import _append_noninteractive_tool_required_message
    items = await _append_noninteractive_tool_required_message(session=None, context={'parent_id': None}, attempt=1, limit=3, root_finish_tool='finish_pr_review')
    assert 'call finish_pr_review' in items[0]['content']
    assert 'finish_scan' not in items[0]['content']
