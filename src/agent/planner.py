"""Planner：把目标转成工具调用计划（Plan）。

LLM 无关设计：LLMPlanner 注入 llm callable（prompt -> json 文本），
ScriptedPlanner 供测试/复现。两路产出都过 validate_plan 硬校验。

计划格式（JSON）：
    {"steps": [{"tool": "...", "args": {...}, "purpose": "..."}, ...],
     "final_answer": "可选，结束闭环"}
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Callable

from .tool_registry import ToolRegistry


@dataclass
class PlanStep:
    tool: str
    args: dict
    purpose: str = ""


@dataclass
class Plan:
    steps: list[PlanStep] = field(default_factory=list)
    final_answer: str | None = None
    raw: str = ""


class PlanValidationError(ValueError):
    pass


def validate_plan(plan: Plan, registry: ToolRegistry,
                  max_steps: int = 8) -> None:
    """计划硬校验（防幻觉/越权/失控在计划期拦截）。

    - 工具名必须在注册表（白名单）；
    - 每步参数通过 schema 校验；
    - 步数 ≤ max_steps（防计划级失控）。
    """
    if not plan.steps and not plan.final_answer:
        raise PlanValidationError("计划为空：无步骤且无 final_answer")
    if len(plan.steps) > max_steps:
        raise PlanValidationError(f"计划 {len(plan.steps)} 步 > 上限 {max_steps}")
    for i, s in enumerate(plan.steps):
        if not registry.has(s.tool):
            raise PlanValidationError(
                f"步骤{i + 1} 引用未注册工具 '{s.tool}'（可用: {registry.names()}）")
        err = registry.validate_args(s.tool, s.args)
        if err:
            raise PlanValidationError(f"步骤{i + 1}({s.tool}) {err}")


def parse_plan(json_text: str, registry: ToolRegistry,
               max_steps: int = 8) -> Plan:
    """解析 LLM 输出为计划（格式容错：剥离 markdown 围栏）+ 硬校验。"""
    text = json_text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanValidationError(f"计划 JSON 解析失败: {exc}") from exc
    if not isinstance(data, dict) or "steps" not in data:
        raise PlanValidationError("计划 JSON 须为含 'steps' 的对象")
    steps = []
    for i, st in enumerate(data["steps"]):
        if not isinstance(st, dict) or "tool" not in st:
            raise PlanValidationError(f"步骤 {i + 1} 缺 'tool' 字段")
        args = st.get("args", {})
        if not isinstance(args, dict):
            raise PlanValidationError(f"步骤 {i + 1} args 须为 dict")
        steps.append(PlanStep(tool=st["tool"], args=args,
                              purpose=str(st.get("purpose", ""))))
    plan = Plan(steps=steps,
                final_answer=(str(data["final_answer"])
                              if data.get("final_answer") is not None else None),
                raw=json_text)
    validate_plan(plan, registry, max_steps)
    return plan


class LLMPlanner:
    """LLM 计划器：prompt 模板 + 可注入 llm callable。"""

    def __init__(self, llm: Callable[[str], str], registry: ToolRegistry,
                 max_steps: int = 8):
        self.llm = llm
        self.registry = registry
        self.max_steps = max_steps

    def make_plan(self, goal: str, observation: str = "") -> Plan:
        prompt = (
            f"目标：{goal}\n\n可用工具：\n{self.registry.describe()}\n\n"
            f"上轮观测：\n{observation or '（首轮，无）'}\n\n"
            "输出 JSON 计划：{\"steps\": [{\"tool\":..., \"args\":{...}, "
            "\"purpose\":...}], \"final_answer\": ...}。"
            f"最多 {self.max_steps} 步；只能用上面列出的工具。")
        return parse_plan(self.llm(prompt), self.registry, self.max_steps)


class ScriptedPlanner:
    """脚本计划器：按序吐出预设计划（测试/复现）。"""

    def __init__(self, plans: list[Plan]):
        if not plans:
            raise ValueError("plans 为空")
        self.plans = list(plans)
        self._i = 0

    def make_plan(self, goal: str, observation: str = "") -> Plan:
        if self._i >= len(self.plans):
            raise RuntimeError("ScriptedPlanner 计划耗尽（循环驱动方需保证终止）")
        p = self.plans[self._i]
        self._i += 1
        return p
