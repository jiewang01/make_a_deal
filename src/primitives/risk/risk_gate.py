"""组合风控闸门：目标权重 → 逐层约束 → 最终权重。

日线 5 层（8 层中日内/杠杆/相关性/波动率层后续版本展开）：
1. gross 总仓位上限
2. 单票权重上限
3. 行业权重上限
4. 流动性参与率（单票权重 ≤ 参与率 × 当日成交额 / 组合总值）
5. 终检（Σ|w| ≤ gross，浮点容差内硬断言）

防攻击面设计：
- 仓位溢出：所有裁剪为"缩小"方向（clip/比例缩放），任何层不得放大权重；
  apply 后终检 sum<=gross+ε，violation 记录每一层的改动。
- 极端行情（amount=0 / NaN）：流动性层视为不可持有，权重置 0。
- 权重含 NaN / 负值（做空）：先清洗为非负（本引擎做多域），NaN→0。
"""
from __future__ import annotations
from dataclasses import dataclass, field
import math
import pandas as pd

_EPS = 1e-9


@dataclass
class RiskConfig:
    gross_limit: float = 0.95        # L1 总仓位上限
    per_stock_limit: float = 0.10    # L2 单票上限
    per_industry_limit: float = 0.30 # L3 行业上限
    participation: float = 0.05      # L4 参与率（当日成交额占比上限）


@dataclass
class GateReport:
    layer: str
    detail: str


@dataclass
class GateResult:
    weights: dict[str, float]
    violations: list[GateReport] = field(default_factory=list)

    @property
    def gross(self) -> float:
        return sum(self.weights.values())


def _clean(w: dict[str, float]) -> dict[str, float]:
    """清洗：去 NaN / 负值（本层做多域，做空属执行层）。"""
    return {k: (v if (v is not None and not math.isnan(v) and v > 0) else 0.0)
            for k, v in w.items()}


def apply_risk_gate(target: dict[str, float], *,
                    industries: dict[str, str] | None = None,
                    amounts: dict[str, float] | None = None,
                    portfolio_value: float | None = None,
                    holdings: dict[str, float] | None = None,
                    cfg: RiskConfig | None = None) -> GateResult:
    """对目标权重逐层施加风控约束。

    Args:
        target: {资产: 目标权重}（正=做多）。
        industries: {资产: 行业标签}，行业层需要。
        amounts: {资产: 当日成交额(元)}，流动性层需要。
        portfolio_value: 组合总值(元)，流动性层需要。
        holdings: {资产: 当前持仓权重}，停牌"禁增不禁持"判定需要
                  （无成交额但已有持仓 → 保留存量并告警，而非强制清零）。
        cfg: 风控参数。
    Returns:
        GateResult：最终权重 + 各层违规记录。
    """
    cfg = cfg or RiskConfig()
    w = _clean(target)
    violations: list[GateReport] = []

    # L1 总仓位：等比例缩放
    gross = sum(w.values())
    if gross > cfg.gross_limit + _EPS:
        scale = cfg.gross_limit / gross
        w = {k: v * scale for k, v in w.items()}
        violations.append(GateReport("L1_gross", f"Σw={gross:.3f}>{cfg.gross_limit} 等比缩放"))

    # L2 单票：clip
    for k, v in w.items():
        if v > cfg.per_stock_limit + _EPS:
            w[k] = cfg.per_stock_limit
            violations.append(GateReport("L2_stock", f"{k}: {v:.3f}>{cfg.per_stock_limit} 截断"))

    # L3 行业：组内等比缩放
    if industries:
        groups: dict[str, dict[str, float]] = {}
        for k, v in w.items():
            groups.setdefault(industries.get(k, "UNKNOWN"), {})[k] = v
        for ind, grp in groups.items():
            s = sum(grp.values())
            if s > cfg.per_industry_limit + _EPS:
                scale = cfg.per_industry_limit / s
                for k in grp:
                    w[k] = w[k] * scale
                violations.append(
                    GateReport("L3_industry", f"{ind}: Σ={s:.3f}>{cfg.per_industry_limit} 组内缩放"))

    # L4 流动性参与率：单票可交易额 = participation × 当日成交额
    # → 权重上限 w_max = participation × amount / portfolio_value
    if amounts and portfolio_value and portfolio_value > 0:
        for k, v in w.items():
            amt = amounts.get(k)
            if amt is None or (isinstance(amt, float) and math.isnan(amt)) or amt <= 0:
                # 修复 A2 停牌禁增不禁持：无成交额时
                # - 新仓（目标 > 持仓）→ 置零（买不进一字无量板）
                # - 存量持仓（holdings 有值且目标 ≤ 持仓）→ 保留持仓权重并告警
                #   （停牌卖不出，强制清零 = 假设能卖出）
                held = (holdings or {}).get(k, 0.0)
                if v > 0:
                    if v <= held + _EPS and held > 0:
                        w[k] = held
                        violations.append(
                            GateReport("L4_liquidity", f"{k}: 停牌禁增不禁持 保留存量{held:.3f}"))
                    else:
                        w[k] = 0.0
                        violations.append(GateReport("L4_liquidity", f"{k}: 无有效成交额 置零"))
                continue
            w_max = min(cfg.per_stock_limit,
                        cfg.participation * amt / portfolio_value)
            if v > w_max + _EPS:
                w[k] = w_max
                violations.append(
                    GateReport("L4_liquidity", f"{k}: {v:.3f}>参与率上限{w_max:.3f} 截断"))

    # L5 终检：防溢出硬校验（浮点容差）
    final = sum(w.values())
    assert final <= cfg.gross_limit + 1e-6, \
        f"风控终检失败：Σw={final} > {cfg.gross_limit}（风控被穿透）"
    # 数值规整：消除 -0.0 / 极小残差
    w = {k: (round(v, 12) if v > 0 else 0.0) for k, v in w.items()}
    return GateResult(weights=w, violations=violations)
