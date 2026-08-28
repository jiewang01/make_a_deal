"""L3 偏差校正：对 Agent 闭环产出 / 回测结论做事后偏差检测。

检测项（全部给出"证据 + 建议"，不自动改结论——人/上层 Agent 复核）：
1. recency_bias：结论论据是否集中在最后 N 轮观测；
2. confirmation_bias：是否存在同结论重复计数（同一证据多次引用）；
3. overfit_risk：IS/OOS 衰减比劣化警报（对接 WFO 结果）；
4. empty_evidence：结论无任何成功工具调用支撑（幻觉警报）。

输出 BiasCheckResult：detected 列表 + corrected_answer（带置信度标注的
结论原文，不篡改结论本身）。
"""
from __future__ import annotations
from dataclasses import dataclass, field

from src.agent import AgentRunResult


@dataclass
class BiasItem:
    kind: str
    evidence: str
    advice: str


@dataclass
class BiasCheckResult:
    detected: list[BiasItem] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.detected

    def summary(self) -> str:
        if self.clean:
            return "未检测到偏差"
        return "\n".join(
            f"[{b.kind}] {b.evidence} → 建议: {b.advice}" for b in self.detected)


def check_recency_bias(result: AgentRunResult, window: int = 2) -> BiasItem | None:
    """近因偏差：最后 window 轮之外的迭代观测被忽略即结论仅靠近期证据。

    判据：迭代数 > window 且早期轮有成功调用，**且结论未引用任何早期
    证据**（final_answer 含早期步骤的参数/结果 token 则视为已引用，
    不告警——避免对正常引用早期证据的结论误报）。
    """
    if len(result.iterations) <= window:
        return None
    early = result.iterations[:-window]
    early_ok = [s for it in early for s in it.steps if s.ok]
    if not early_ok:
        return None
    # 结论引用检查：早期步骤的 args 值 / 结果文本片段出现在结论中即视为引用
    answer = (result.final_answer or "").lower()
    evidence_tokens: set[str] = set()
    for s in early_ok:
        for v in s.args.values():
            evidence_tokens.update(str(v).lower().split())
        for tok in s.result_preview.lower().split():
            if len(tok) >= 3:  # 短 token（如符号）跳过防误匹配
                evidence_tokens.add(tok)
    if answer and any(tok in answer for tok in evidence_tokens):
        return None
    return BiasItem(
        "recency_bias",
        f"前 {len(early)} 轮含 {len(early_ok)} 次成功调用，结论可能仅基于最近 {window} 轮",
        "复核早期观测是否支持同一结论")


def check_confirmation_bias(result: AgentRunResult) -> BiasItem | None:
    """确认偏差：同一步骤签名（工具+参数）被重复**成功**执行多次。

    失败重试不计入（重试是纠错不是堆叠证据）；只有成功的重复调用
    才可能是"重复计数同一证据"的确认偏差。
    """
    from collections import Counter
    sigs = Counter()
    for it in result.iterations:
        for s in it.steps:
            if s.ok:
                sigs[(s.tool, json_key(s.args))] += 1
    dup = {sig: n for sig, n in sigs.items() if n > 1}
    if not dup:
        return None
    worst = max(dup.items(), key=lambda x: x[1])
    return BiasItem(
        "confirmation_bias",
        f"步骤 {worst[0][0]} 重复执行 {worst[1]} 次（{len(dup)} 个重复签名）",
        "重复调用不增加证据强度，检查是否在堆叠确认性证据")


def json_key(args: dict) -> str:
    import json
    return json.dumps(args, ensure_ascii=False, sort_keys=True)


def check_empty_evidence(result: AgentRunResult) -> BiasItem | None:
    """空证据（幻觉警报）：有结论但无任何成功工具调用。"""
    n_ok = sum(1 for it in result.iterations for s in it.steps if s.ok)
    if result.final_answer and n_ok == 0:
        return BiasItem(
            "empty_evidence",
            f"结论 '{result.final_answer[:50]}' 无任何成功工具调用支撑",
            "结论视为未验证假设，需工具证据支持")
    return None


def check_overfit_risk(decay_ratio: float | None,
                       oos_score: float | None) -> BiasItem | None:
    """过拟合风险：decay < 0.5 或 OOS 实亏。"""
    if decay_ratio is not None and decay_ratio < 0.5:
        return BiasItem(
            "overfit_risk",
            f"IS/OOS 衰减比 {decay_ratio:.2f} < 0.5",
            "样本外衰减过半，参数可能拟合历史")
    if oos_score is not None and oos_score < 0:
        return BiasItem(
            "overfit_risk",
            f"OOS 分数 {oos_score:.3f} < 0",
            "样本外实亏，结论不应基于样本内表现")
    return None


def run_bias_checks(result: AgentRunResult, *,
                    decay_ratio: float | None = None,
                    oos_score: float | None = None,
                    recency_window: int = 2) -> BiasCheckResult:
    """全量偏差检测入口。"""
    checks = [
        check_recency_bias(result, recency_window),
        check_confirmation_bias(result),
        check_empty_evidence(result),
        check_overfit_risk(decay_ratio, oos_score),
    ]
    return BiasCheckResult([c for c in checks if c is not None])
