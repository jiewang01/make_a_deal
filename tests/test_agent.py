"""v0.7.0 L3 Agent 最小闭环测试：工具注册/计划校验/闭环失控防护。"""
import json
import pytest

from src.agent import (
    ToolRegistry, LLMPlanner, ScriptedPlanner, Plan, PlanStep,
    PlanValidationError, parse_plan, AgentLoop,
)


@pytest.fixture
def registry():
    r = ToolRegistry()
    r.register("get_price", lambda code, days: {"close": [10.0] * days},
               desc="取收盘价", arg_schema={
                   "code": {"type": "str"}, "days": {"type": "int"}})
    r.register("echo", lambda text: text, desc="回显",
               arg_schema={"text": {"type": "str"}})
    return r


# ---------------------------------------------------------------- 工具注册
def test_重复注册报错(registry):
    with pytest.raises(ValueError, match="已注册"):
        registry.register("echo", lambda text: text)


def test_未注册工具被拒(registry):
    res = registry.call("delete_database", {})
    assert not res.ok
    assert "未知工具" in res.error


def test_参数校验_类型与缺失(registry):
    assert "缺参数" in registry.validate_args("get_price", {"code": "600519"})
    assert "须 int" in registry.validate_args("get_price", {"code": "600519", "days": "5"})
    assert "须 str" in registry.validate_args("get_price", {"code": 600519, "days": 5})
    assert registry.validate_args("get_price", {"code": "600519", "days": 5}) is None
    # int 可升 float（宽松）；bool 不可冒充 int
    r2 = ToolRegistry()
    r2.register("f", lambda x: x, arg_schema={"x": {"type": "float"}})
    assert r2.validate_args("f", {"x": 3}) is None
    r3 = ToolRegistry()
    r3.register("g", lambda x: x, arg_schema={"x": {"type": "int"}})
    assert "bool" in r3.validate_args("g", {"x": True})


def test_工具异常转结构化错误():
    r = ToolRegistry()
    r.register("boom", lambda: 1 / 0)
    res = r.call("boom", {})
    assert not res.ok
    assert "ZeroDivisionError" in res.error


def test_结果截断():
    r = ToolRegistry()
    r.register("big", lambda: "x" * 10_000, max_result_chars=100)
    res = r.call("big", {})
    assert res.ok and res.truncated
    assert len(res.value) < 200
    assert "截断" in res.value


# ---------------------------------------------------------------- 计划校验
def test_解析含围栏的JSON(registry):
    text = '```json\n{"steps": [{"tool": "echo", "args": {"text": "hi"}}], "final_answer": "done"}\n```'
    plan = parse_plan(text, registry)
    assert plan.steps[0].tool == "echo"
    assert plan.final_answer == "done"


def test_非法JSON报错(registry):
    with pytest.raises(PlanValidationError, match="JSON"):
        parse_plan("not json at all", registry)


def test_计划引用未注册工具被拒(registry):
    plan = Plan(steps=[PlanStep(tool="hack", args={})])
    with pytest.raises(PlanValidationError, match="未注册工具"):
        parse_plan(json.dumps({"steps": [{"tool": "hack"}]}), registry)


def test_计划参数非法被拒(registry):
    with pytest.raises(PlanValidationError, match="须 int"):
        parse_plan(json.dumps(
            {"steps": [{"tool": "get_price", "args": {"code": "600519", "days": "5"}}]}),
            registry)


def test_计划步数超限被拒(registry):
    steps = [{"tool": "echo", "args": {"text": "x"}}] * 9
    with pytest.raises(PlanValidationError, match="上限"):
        parse_plan(json.dumps({"steps": steps}), registry, max_steps=8)


def test_空计划被拒(registry):
    with pytest.raises(PlanValidationError, match="为空"):
        parse_plan(json.dumps({"steps": []}), registry)


# ---------------------------------------------------------------- 闭环
def _scripted(registry, plans):
    return ScriptedPlanner(plans)


def test_闭环_正常完成():
    r = ToolRegistry()
    r.register("echo", lambda text: text, arg_schema={"text": {"type": "str"}})
    plans = [Plan(steps=[PlanStep("echo", {"text": "hi"})], final_answer="答案")]
    res = AgentLoop(_scripted(r, plans), r).run("goal")
    assert res.stop_reason == "completed"
    assert res.final_answer == "答案"
    assert res.n_tool_calls == 1


def test_闭环_迭代多轮后完成():
    r = ToolRegistry()
    state = {"n": 0}

    def counter(text):
        state["n"] += 1
        return f"count={state['n']}"

    r.register("count", lambda: counter("x"))
    plans = [
        Plan(steps=[PlanStep("count", {})]),
        Plan(steps=[PlanStep("count", {})]),
        Plan(steps=[], final_answer="done after 2"),
    ]
    res = AgentLoop(_scripted(r, plans), r).run("goal")
    assert res.stop_reason == "completed"
    assert res.n_tool_calls == 2
    assert len(res.iterations) == 3


def test_无限循环_迭代上限(registry):
    # 每轮同一计划、无 final_answer → 应停在 max_iterations 而非死循环
    echo_plan = Plan(steps=[PlanStep("echo", {"text": "same"})])
    planner = _ScriptedForever(echo_plan)
    res = AgentLoop(planner, registry, max_iterations=3).run("goal")
    assert res.stop_reason in ("max_iterations", "stalled")
    assert len(res.iterations) <= 3


class _ScriptedForever:
    """无限吐同一计划（模拟 LLM 死循环倾向）。"""

    def __init__(self, plan):
        self.plan = plan

    def make_plan(self, goal, observation=""):
        return self.plan


def test_无限循环_总调用预算(registry):
    plan = Plan(steps=[PlanStep("echo", {"text": f"call"})] * 5)  # 每轮 5 调用
    res = AgentLoop(_ScriptedForever(plan), registry,
                    max_iterations=100, max_total_calls=12).run("goal")
    assert res.n_tool_calls <= 12
    assert res.stop_reason in ("budget_exceeded", "stalled")


def test_停滞检测_相同观测(registry):
    # 连续同工具同参数同结果 → stalled
    plan = Plan(steps=[PlanStep("echo", {"text": "same"})])
    res = AgentLoop(_ScriptedForever(plan), registry,
                    max_iterations=10, stall_tolerance=2).run("goal")
    assert res.stop_reason == "stalled"
    assert res.n_tool_calls <= 3  # tolerance=2 → 最多 3 次相同观测即停


def test_计划非法_不崩溃_记planner_error(registry):
    class BadPlanner:
        def make_plan(self, goal, observation=""):
            raise PlanValidationError("bad plan")

    res = AgentLoop(BadPlanner(), registry, max_iterations=2).run("goal")
    assert res.stop_reason == "planner_error"
    assert res.n_tool_calls == 0
    assert "计划非法" in res.iterations[0].error
    # 首轮非法即终止：不再消耗后续迭代
    assert len(res.iterations) == 1


def test_LLMPlanner_注入callable_端到端():
    r = ToolRegistry()
    r.register("echo", lambda text: text, desc="回显",
               arg_schema={"text": {"type": "str"}})

    def fake_llm(prompt):
        # 验证 prompt 含工具清单与目标
        assert "echo" in prompt and "分析" in prompt
        return json.dumps({
            "steps": [{"tool": "echo", "args": {"text": "ok"}, "purpose": "测试"}],
            "final_answer": "完成"})

    planner = LLMPlanner(fake_llm, r)
    res = AgentLoop(planner, r).run("分析目标")
    assert res.stop_reason == "completed"
    assert res.final_answer == "完成"


def test_观测文本截断_token防护(registry):
    r = ToolRegistry()
    r.register("big", lambda: "y" * 5000, max_result_chars=5000)
    plan = Plan(steps=[PlanStep("big", {})])
    loop = AgentLoop(_ScriptedForever(plan), r, max_observation_chars=100,
                     stall_tolerance=1)
    res = loop.run("goal")
    # 截断后观测进入下一轮 prompt 不超限
    assert res.stop_reason in ("stalled", "max_iterations")
    assert loop.max_observation_chars == 100


# ---------------------------------------------------------------- Attacker 回归
def test_A1_非str大结果观测有界():
    """dict 大结果绕过 str 截断 → 观测每步仍须 ≤ ~500 字符。"""
    r = ToolRegistry()
    r.register("big_dict", lambda: {"data": list(range(10_000))})
    plan = Plan(steps=[PlanStep("big_dict", {})])
    loop = AgentLoop(_ScriptedForever(plan), r, stall_tolerance=1,
                     max_iterations=2)
    res = loop.run("goal")
    # 单步观测被截断（< 1000 字符），不随结果规模膨胀
    obs = loop._step_observation(
        PlanStep("big_dict", {}), r.call("big_dict", {}))
    assert len(obs) < 1000


def test_A2_预算截断不丢弃已有终答():
    """计划含 final_answer 且末步预算耗尽 → 仍按 completed 上报终答。"""
    r = ToolRegistry()
    r.register("echo", lambda text: text, arg_schema={"text": {"type": "str"}})
    plan = Plan(steps=[PlanStep("echo", {"text": "x"})] * 3,
                final_answer="最终结论")
    res = AgentLoop(_ScriptedForever(plan), r, max_total_calls=2,
                    stall_tolerance=1).run("goal")
    assert res.n_tool_calls == 2                 # 预算生效
    assert res.stop_reason == "completed"        # A2: 终答不丢弃
    assert res.final_answer == "最终结论"
    # 截断信息保留在迭代记录
    assert "预算耗尽" in res.iterations[-1].error


def test_A3_stall_tolerance零被拒():
    r = ToolRegistry()
    with pytest.raises(ValueError, match="stall_tolerance"):
        AgentLoop(_ScriptedForever(Plan(steps=[], final_answer="x")),
                  r, stall_tolerance=0)


def test_A4_未知参数计划期拦截():
    """schema 外参数在 validate_args / parse_plan 双层被拒。"""
    r = ToolRegistry()
    r.register("echo", lambda text: text, arg_schema={"text": {"type": "str"}})
    # 直接调工具
    res = r.call("echo", {"text": "hi", "cmd": "rm -rf /"})
    assert not res.ok
    assert "未知参数" in res.error
    # 经计划解析
    with pytest.raises(PlanValidationError, match="未知参数"):
        parse_plan(json.dumps({
            "steps": [{"tool": "echo", "args": {"text": "hi", "cmd": "evil"}}]}),
            r)
