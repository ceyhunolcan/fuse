from __future__ import annotations
import pytest
from agents.tool import FunctionTool
from strix.agents import factory

def _tool(name: str) -> FunctionTool:
    return FunctionTool(name=name, description='test tool', params_json_schema={'type': 'object', 'properties': {}, 'additionalProperties': False}, on_invoke_tool=lambda _ctx, _inp: 'ok')

def test_root_ends_with_the_given_finish_tool() -> None:
    root = factory.build_strix_agent(is_root=True, finish_tool=_tool('finish_pr_review'))
    root_names = [t.name for t in root.tools]
    assert root_names[-1] == 'finish_pr_review'
    assert 'finish_scan' not in root_names
