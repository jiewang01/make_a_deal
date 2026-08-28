"""L3 Agent 最小闭环（v0.7.0）：tool_registry + planner + 四阶循环。

公开接口：
- ToolRegistry / Tool / ToolCallResult: 白名单工具注册与调用
- LLMPlanner / ScriptedPlanner / parse_plan / validate_plan: 计划生成与校验
- AgentLoop / AgentRunResult: 假设→验证→解读→迭代闭环
"""
from .tool_registry import ToolRegistry, Tool, ToolCallResult
from .planner import (
    LLMPlanner, ScriptedPlanner, Plan, PlanStep, PlanValidationError,
    parse_plan, validate_plan,
)
from .loop import AgentLoop, AgentRunResult, StepRecord, IterationRecord

__all__ = [
    "ToolRegistry", "Tool", "ToolCallResult",
    "LLMPlanner", "ScriptedPlanner", "Plan", "PlanStep", "PlanValidationError",
    "parse_plan", "validate_plan",
    "AgentLoop", "AgentRunResult", "StepRecord", "IterationRecord",
]
