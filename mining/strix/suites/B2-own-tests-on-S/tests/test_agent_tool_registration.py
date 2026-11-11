from __future__ import annotations
import pytest
from agents.tool import FunctionTool
from strix.agents import factory

def _tool(name: str) -> FunctionTool:
    return FunctionTool(name=name, description='test tool', params_json_schema={'type': 'object', 'properties': {}, 'additionalProperties': False}, on_invoke_tool=lambda _ctx, _inp: 'ok')

def test_report_tool_replaces_create_vulnerability_report() -> None:
    for is_root in (True, False):
        agent = factory.build_strix_agent(is_root=is_root, report_tool=_tool('file_pr_finding'))
        names = [t.name for t in agent.tools]
        assert 'file_pr_finding' in names
        assert 'create_vulnerability_report' not in names
        assert isinstance(agent.instructions, str)
        assert 'file_pr_finding' in agent.instructions
        assert 'create_vulnerability_report' not in agent.instructions
