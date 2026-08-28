"""四阶闭环：假设(计划) → 验证(执行) → 解读(观测) → 迭代(再计划)。

失控防护（v0.7.0 攻击面）：
- 无限循环：max_iterations（迭代轮上限）+ 总调用数预算 max_total_calls
  + 停滞检测（连续 N 轮观测不变 → 判 stalled）；
- token 失控：观测文本超 max_observation_chars 截断后才进入下一轮 prompt；
- 每轮异常（计划非法/工具失败）不崩溃：进入下一轮并记录，预算耗尽终止。
"""
from __future__ import annotations
from dataclasses import dataclass, field

from .tool_registry import ToolRegistry, ToolCallResult
from .planner import Plan, PlanValidationError


@dataclass
class StepRecord:
    iteration: int
    tool: str
    args: dict
    ok: bool
    result_preview: str
    error: str = ""


@dataclass
class IterationRecord:
    iteration: int
    plan_raw: str
    steps: list[StepRecord] = field(default_factory=list)
    error: str = ""


@dataclass
class AgentRunResult:
    goal: str
    final_answer: str | None
    stop_reason: str            # completed / max_iterations / budget_exceeded / stalled / planner_error
    iterations: list[IterationRecord]
    n_tool_calls: int
    total_result_chars: int


class AgentLoop:
    """四阶闭环执行器。

    用法：
        loop = AgentLoop(planner, registry, max_iterations=5)
        res = loop.run("评估 600519 近一年均线交叉策略")
    """

    def __init__(self, planner, registry: ToolRegistry, *,
                 max_iterations: int = 5,
                 max_total_calls: int = 20,
                 max_observation_chars: int = 6000,
                 stall_tolerance: int = 2):
        if max_iterations < 1 or max_total_calls < 1:
            raise ValueError("max_iterations/max_total_calls 须 >= 1")
        # 修复 A3：stall_tolerance=0 时 [-1:] 单元素 all() 恒 True → 任何观测即停滞
        if stall_tolerance < 1:
            raise ValueError("stall_tolerance 须 >= 1（0 会恒判停滞）")
        self.planner = planner
        self.registry = registry
        self.max_iterations = max_iterations
        self.max_total_calls = max_total_calls
        self.max_observation_chars = max_observation_chars
        self.stall_tolerance = stall_tolerance

    def run(self, goal: str) -> AgentRunResult:
        iterations: list[IterationRecord] = []
        observations: list[str] = []
        n_calls = 0
        total_chars = 0
        final_answer = None
        stop_reason = "max_iterations"

        for it in range(1, self.max_iterations + 1):
            obs_text = self._compose_observation(observations)
            # ① 假设：生成计划
            try:
                plan = self.planner.make_plan(goal, obs_text)
            except PlanValidationError as exc:
                iterations.append(IterationRecord(
                    it, "", [], f"[计划非法] {exc}"))
                stop_reason = "planner_error"
                break
            except Exception as exc:
                iterations.append(IterationRecord(
                    it, "", [], f"[计划器异常] {type(exc).__name__}: {exc}"))
                stop_reason = "planner_error"
                break

            rec = IterationRecord(it, plan.raw)
            # ② 验证：执行步骤
            for step in plan.steps:
                if n_calls >= self.max_total_calls:
                    rec.error = "[预算耗尽] 步骤被截断"
                    stop_reason = "budget_exceeded"
                    break
                res: ToolCallResult = self.registry.call(step.tool, step.args)
                n_calls += 1
                preview = self._preview(res)
                total_chars += len(preview)
                rec.steps.append(StepRecord(
                    it, step.tool, step.args, res.ok, preview, res.error))
                observations.append(self._step_observation(step, res))

            iterations.append(rec)
            if stop_reason == "budget_exceeded":
                # 修复 A2：预算耗尽时若计划已含终答仍按完成上报（部分步骤
                # 未执行但不静默丢弃 LLM 结论；截断信息保留在 rec.error）
                if plan.final_answer is not None:
                    final_answer = plan.final_answer
                    stop_reason = "completed"
                break

            # ③ 解读：LLM 给出终答则闭环完成
            if plan.final_answer is not None:
                final_answer = plan.final_answer
                stop_reason = "completed"
                break

            # ④ 迭代：停滞检测（连续同观测）
            if self._is_stalled(observations):
                stop_reason = "stalled"
                break

        return AgentRunResult(goal, final_answer, stop_reason, iterations,
                              n_calls, total_chars)

    # ---------------------------------------------------------------- helpers
    def _compose_observation(self, observations: list[str]) -> str:
        """拼装观测文本并截断（token 失控防护）。"""
        text = "\n".join(observations[-self.stall_tolerance * 2:])
        if len(text) > self.max_observation_chars:
            text = text[:self.max_observation_chars] + "\n[...观测截断]"
        return text

    def _step_observation(self, step: PlanStep, res: ToolCallResult) -> str:
        """单步观测文本（修复 A1：非 str 结果同样截断，防 token 失控）。"""
        if res.ok:
            v = res.value if isinstance(res.value, str) else repr(res.value)
            if len(v) > 400:
                v = v[:400] + "[...观测截断]"
            return f"{step.tool}({step.args}) -> {v}"
        err = res.error if len(res.error) <= 400 else res.error[:400] + "[...截断]"
        return f"{step.tool}({step.args}) 失败: {err}"

    def _preview(self, res: ToolCallResult) -> str:
        if not res.ok:
            return res.error
        v = res.value if isinstance(res.value, str) else repr(res.value)
        return v[:200] + ("..." if len(v) > 200 else "")

    def _is_stalled(self, observations: list[str]) -> bool:
        """停滞检测：最近 stall_tolerance+1 条观测完全相同 → 判定停滞。

        注意：连续相同工具+相同成功结果即停滞（失败结果会进入下一轮
        重试观测相同，也计入停滞——防止失败重试死循环）。
        """
        k = self.stall_tolerance
        if len(observations) < k + 1:
            return False
        recent = observations[-(k + 1):]
        return all(o == recent[0] for o in recent)
