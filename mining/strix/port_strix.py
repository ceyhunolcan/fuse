"""Port B1 (#1496) and B2 (#1550) of the Strix development triple onto S+A (#733), each alone.

Strix (usestrix/strix, Apache-2.0) changed a lot between A (11 July 2026) and the two Bs (8 and 20 October 2026):
55 commits touch the shared files, so the upstream diffs do not apply to S+A. Each port re-expresses its B's change
as exact text edits on the S+A tree (every old string must occur exactly once), keeping B's names, signatures,
defaults and messages. Where S+A lacks the code a B also edited, that part is left out and listed in PORT_NOTES.
Tests are B's own added tests, verbatim, except where noted. Text-only operations; no repository code is executed.
Usage: python3 port_strix.py <strix clone>   ->  patches/B1-1496-port.patch, patches/B2-1550-port.patch
"""
import shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(sys.argv[1]).resolve()
A = '205e0b370706283cee4bcd6d7d8ca7faf41e9d23'

FACTORY, RUNNER, PROMPT = 'strix/agents/factory.py', 'strix/core/runner.py', 'strix/agents/prompt.py'
REG_TEST, EXEC_TEST = 'tests/test_agent_tool_registration.py', 'tests/test_execution.py'

B1_TEST_REG = '''def test_root_ends_with_the_given_finish_tool() -> None:
    root = factory.build_strix_agent(is_root=True, finish_tool=_tool("finish_pr_review"))

    root_names = [t.name for t in root.tools]

    assert root_names[-1] == "finish_pr_review"
    assert "finish_scan" not in root_names


'''
# Upstream calls execution._append_tool_required_message(..., interactive=False, ...); at S+A the same
# non-interactive nudge is _append_noninteractive_tool_required_message, which has no interactive argument.
B1_TEST_EXEC = '''

@pytest.mark.asyncio
async def test_nudge_names_the_root_finish_tool() -> None:
    from strix.core.execution import _append_noninteractive_tool_required_message

    items = await _append_noninteractive_tool_required_message(
        session=None,
        context={"parent_id": None},
        attempt=1,
        limit=3,
        root_finish_tool="finish_pr_review",
    )

    assert "call finish_pr_review" in items[0]["content"]
    assert "finish_scan" not in items[0]["content"]
'''
B2_TEST_REG = '''def test_report_tool_replaces_create_vulnerability_report() -> None:
    for is_root in (True, False):
        agent = factory.build_strix_agent(is_root=is_root, report_tool=_tool("file_pr_finding"))
        names = [t.name for t in agent.tools]
        assert "file_pr_finding" in names
        assert "create_vulnerability_report" not in names
        assert isinstance(agent.instructions, str)
        assert "file_pr_finding" in agent.instructions
        assert "create_vulnerability_report" not in agent.instructions


'''

B1 = {
    FACTORY: [
        ('    instructions_override: str | None = None,\n) -> SandboxAgent[Any]:',
         '    instructions_override: str | None = None,\n    finish_tool: Tool = finish_scan,\n) -> SandboxAgent[Any]:'),
        ('            of rendering the built-in scan prompt.\n',
         '            of rendering the built-in scan prompt.\n'
         '        finish_tool: The tool that ends the run, given to the root agent.\n'),
        ('tools: list[Tool] = [*_BASE_TOOLS, *agent_tools, finish_scan]',
         'tools: list[Tool] = [*_BASE_TOOLS, *agent_tools, finish_tool]'),
    ],
    'strix/core/agents.py': [
        ('        self.is_shutting_down = False\n',
         '        self.is_shutting_down = False\n        self.root_finish_tool = "finish_scan"\n'),
    ],
    'strix/core/execution.py': [
        ('    attempt: int,\n    limit: int,\n) -> list[dict[str, str]]:\n'
         '    finish_tool = "finish_scan" if context.get("parent_id") is None else "agent_finish"\n',
         '    attempt: int,\n    limit: int,\n    root_finish_tool: str = "finish_scan",\n) -> list[dict[str, str]]:\n'
         '    finish_tool = root_finish_tool if context.get("parent_id") is None else "agent_finish"\n'),
        ('            context=context,\n            attempt=invalid_final_outputs,\n',
         '            context=context,\n            root_finish_tool=coordinator.root_finish_tool,\n'
         '            attempt=invalid_final_outputs,\n'),
    ],
    'strix/tools/agents_graph/tools.py': [
        ('                    "agent_finish is for subagents. Root/main agents must call finish_scan instead"\n',
         '                    "agent_finish is for subagents. Root/main agents must call "\n'
         '                    f"{coordinator.root_finish_tool} instead"\n'),
    ],
    RUNNER: [
        ('from strix.telemetry.logging import set_scan_id, setup_scan_logging\n',
         'from strix.telemetry.logging import set_scan_id, setup_scan_logging\n'
         'from strix.tools.finish.tool import finish_scan\n'),
        ('    from agents.result import RunResultBase\n',
         '    from agents.result import RunResultBase\n    from agents.tool import Tool\n'),
        ('    event_sink: StreamEventSink | None = None,\n) -> RunResultBase | None:\n'
         '    """Run or resume one Strix scan against a sandbox."""\n',
         '    event_sink: StreamEventSink | None = None,\n    root_finish_tool: Tool = finish_scan,\n'
         ') -> RunResultBase | None:\n'
         '    """Run or resume one Strix scan against a sandbox.\n\n'
         '    ``root_finish_tool`` is the tool the root agent ends the run with.\n    """\n'),
        ('    coordinator.set_snapshot_path(agents_path)\n',
         '    coordinator.set_snapshot_path(agents_path)\n'
         '    coordinator.root_finish_tool = root_finish_tool.name\n'),
        ('            system_prompt_context=scope_context,\n        )\n\n        if not is_resume:',
         '            system_prompt_context=scope_context,\n            finish_tool=root_finish_tool,\n'
         '        )\n\n        if not is_resume:'),
    ],
    REG_TEST: [
        ('def test_per_call_extra_tools_stack_with_registry() -> None:',
         B1_TEST_REG + 'def test_per_call_extra_tools_stack_with_registry() -> None:'),
    ],
}

B2 = {
    FACTORY: [
        ('    instructions_override: str | None = None,\n) -> SandboxAgent[Any]:',
         '    instructions_override: str | None = None,\n'
         '    report_tool: Tool = create_vulnerability_report,\n) -> SandboxAgent[Any]:'),
        ('            of rendering the built-in scan prompt.\n',
         '            of rendering the built-in scan prompt.\n'
         '        report_tool: The tool every agent files findings with, in place of\n'
         '            ``create_vulnerability_report``.\n'),
        ('            system_prompt_context=system_prompt_context,\n        )\n\n    agent_tools',
         '            system_prompt_context=system_prompt_context,\n            report_tool=report_tool.name,\n'
         '        )\n\n    agent_tools'),
        ('    if is_root:\n        tools: list[Tool] = [*_BASE_TOOLS, *agent_tools, finish_scan]\n'
         '    else:\n        tools = [*_BASE_TOOLS, *agent_tools, agent_finish]\n',
         '    base_tools = [report_tool if t is create_vulnerability_report else t for t in _BASE_TOOLS]\n'
         '    if is_root:\n        tools: list[Tool] = [*base_tools, *agent_tools, finish_scan]\n'
         '    else:\n        tools = [*base_tools, *agent_tools, agent_finish]\n'),
        ('    system_prompt_context: dict[str, Any] | None = None,\n) -> Any:',
         '    system_prompt_context: dict[str, Any] | None = None,\n'
         '    report_tool: Tool = create_vulnerability_report,\n) -> Any:'),
        ('            system_prompt_context=system_prompt_context,\n        )\n\n    return _factory',
         '            system_prompt_context=system_prompt_context,\n            report_tool=report_tool,\n'
         '        )\n\n    return _factory'),
    ],
    PROMPT: [
        ('    system_prompt_context: dict[str, Any] | None = None,\n) -> str:',
         '    system_prompt_context: dict[str, Any] | None = None,\n'
         '    report_tool: str = "create_vulnerability_report",\n) -> str:'),
        ('            system_prompt_context=system_prompt_context or {},\n            **skill_content,',
         '            system_prompt_context=system_prompt_context or {},\n            report_tool=report_tool,\n'
         '            **skill_content,'),
    ],
    # every mention of the report tool in the prompt template becomes {{ report_tool }} (4 at S+A)
    'strix/agents/prompts/system_prompt.jinja': 'REPLACE_ALL',
    RUNNER: [
        ('from strix.telemetry.logging import set_scan_id, setup_scan_logging\n',
         'from strix.telemetry.logging import set_scan_id, setup_scan_logging\n'
         'from strix.tools.reporting.tool import create_vulnerability_report\n'),
        ('    from agents.result import RunResultBase\n',
         '    from agents.result import RunResultBase\n    from agents.tool import Tool\n'),
        ('    event_sink: StreamEventSink | None = None,\n) -> RunResultBase | None:\n'
         '    """Run or resume one Strix scan against a sandbox."""\n',
         '    event_sink: StreamEventSink | None = None,\n    report_tool: Tool = create_vulnerability_report,\n'
         ') -> RunResultBase | None:\n'
         '    """Run or resume one Strix scan against a sandbox.\n\n'
         '    ``report_tool`` is the tool every agent files findings with, in place of\n'
         '    ``create_vulnerability_report``.\n    """\n'),
        ('            system_prompt_context=scope_context,\n        )\n\n        if not is_resume:',
         '            system_prompt_context=scope_context,\n            report_tool=report_tool,\n'
         '        )\n\n        if not is_resume:'),
        ('            system_prompt_context=scope_context,\n        )\n\n        async def spawn_child_agent',
         '            system_prompt_context=scope_context,\n            report_tool=report_tool,\n'
         '        )\n\n        async def spawn_child_agent'),
    ],
    REG_TEST: [
        ('def test_instructions_override_is_used_verbatim() -> None:',
         B2_TEST_REG + 'def test_instructions_override_is_used_verbatim() -> None:'),
    ],
}

PORT_NOTES = {
    'B1': [
        'Kept: build_strix_agent(finish_tool=finish_scan) on the line A wrote; run_strix_scan(root_finish_tool=...); '
        'AgentCoordinator.root_finish_tool; the non-interactive nudge and the agent_finish error name the root finish tool.',
        'Left out (code absent at S+A): the budget-reserve notice and the graduated wrap-up directives, and their tests '
        '(test_reserve_notice_names_the_root_finish_tool, test_root_directive_names_the_coordinators_finish_tool).',
        'Test adapted: test_nudge_names_the_root_finish_tool calls the S+A nudge function '
        '_append_noninteractive_tool_required_message (no interactive argument); assertions unchanged.',
    ],
    'B2': [
        'Kept: build_strix_agent and make_child_factory(report_tool=create_vulnerability_report), substituted in the '
        'tool list on the lines A wrote; render_system_prompt(report_tool=...) and {{ report_tool }} in the template; '
        'run_strix_scan(report_tool=...) passed to root and child builders. Test verbatim.',
        'Left out (code absent at S+A): _compose_root_instructions_override, and the counterevidence skill wording '
        '(skill file does not exist at S+A). B1\'s finish_tool is not included, so the root list keeps finish_scan.',
    ],
}


def git(*a, cwd, **k):
    return subprocess.run(['git', *a], cwd=cwd, check=True, capture_output=True, text=True, **k).stdout


def port(name, edits, tests_exec=None):
    with tempfile.TemporaryDirectory() as tmp:
        wt = Path(tmp) / 'w'
        git('worktree', 'add', '-q', '--detach', str(wt), A, cwd=REPO)
        try:
            for path, subs in edits.items():
                f = wt / path
                s = f.read_text()
                if subs == 'REPLACE_ALL':
                    n = s.count('create_vulnerability_report')
                    assert n == 4, (name, path, n)
                    s = s.replace('create_vulnerability_report', '{{ report_tool }}')
                else:
                    for old, new in subs:
                        assert s.count(old) == 1, (name, path, old[:60], s.count(old))
                        s = s.replace(old, new)
                f.write_text(s)
            if tests_exec:
                f = wt / EXEC_TEST
                f.write_text(f.read_text() + tests_exec)
            git('add', '-A', cwd=wt)
            out = HERE / 'patches' / f'{name}-port.patch'
            out.write_text(git('diff', '--cached', '--binary', A, cwd=wt))
        finally:
            git('worktree', 'remove', '--force', str(wt), cwd=REPO)
    return out


if __name__ == '__main__':
    (HERE / 'patches').mkdir(exist_ok=True)
    for p in (port('B1-1496', B1, B1_TEST_EXEC), port('B2-1550', B2)):
        print(p.name, p.stat().st_size)
