from dataclasses import replace
from types import SimpleNamespace

import pytest

from oa.agent_profiles import AgentAssignment
from oa.agent_runtime import (
    RiskLevel,
    RuntimeAssemblyError,
    ToolMode,
    assemble_agent_runtime,
)


def assignment(*tool_ids):
    return AgentAssignment(
        profile_id="employee",
        display_name="测试智能体",
        mission="测试最小权限运行时",
        principal_id="u-test",
        role_name="员工",
        department="测试部",
        led_departments=(),
        direct_report_count=0,
        tool_ids=frozenset(tool_ids),
        skill_ids=("leave-self-service", "policy-guidance"),
        operations=frozenset({"apply_leave"}),
        query_grants=frozenset({"requests:mine"}),
    )


def tool(name):
    return SimpleNamespace(name=name)


def test_runtime_assembles_only_assigned_tools_with_policies():
    current = assignment("policy.read", "personal.query", "leave.apply")
    runtime = assemble_agent_runtime(current, {
        "policy.read": tool("get_leave_policy"),
        "personal.query": tool("query_oa"),
        "leave.apply": tool("apply_leave"),
        # A registered implementation is not exposed unless the profile binds it.
        "approval.prepare": tool("prepare_approval_action"),
    })

    assert runtime.tool_names == ("get_leave_policy", "query_oa", "apply_leave")
    assert "prepare_approval_action" not in runtime.tool_names
    assert runtime.bindings[0].policy.mode == ToolMode.REFERENCE
    assert runtime.bindings[-1].policy.risk == RiskLevel.HIGH
    assert runtime.bindings[-1].policy.requires_confirmation is True


def test_runtime_harness_separates_simple_questions_from_business_actions():
    runtime = assemble_agent_runtime(
        assignment("policy.read", "leave.apply"),
        {"policy.read": tool("get_leave_policy"), "leave.apply": tool("apply_leave")},
    )

    prompt = runtime.prompt_block()
    assert "普通问题直接自然回答" in prompt
    assert "不要创建计划、调用无关查询或展示业务卡片" in prompt
    assert "只生成一项可审计预览" in prompt
    assert "必须由用户确认卡确认" in prompt
    assert "个人 OA 申请办理" in prompt


@pytest.mark.parametrize(
    ("configured", "registry", "message"),
    [
        (("unknown.tool",), {"unknown.tool": tool("unknown")}, "未登记的 Agent 工具策略"),
        (("policy.read",), {}, "Agent 工具实现缺失"),
    ],
)
def test_runtime_fails_closed_for_invalid_configuration(configured, registry, message):
    with pytest.raises(RuntimeAssemblyError, match=message):
        assemble_agent_runtime(assignment(*configured), registry)


def test_runtime_rejects_ambiguous_model_tool_names():
    current = replace(
        assignment("policy.read", "personal.query"),
        profile_id="broken-profile",
    )
    with pytest.raises(RuntimeAssemblyError, match="工具名称冲突"):
        assemble_agent_runtime(current, {
            "policy.read": tool("same_tool"),
            "personal.query": tool("same_tool"),
        })
