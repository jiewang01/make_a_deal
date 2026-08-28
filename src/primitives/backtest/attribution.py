"""Brinson 归因（BHB 模型）：行业 配置 / 选股 / 交互 三分解。

输入为单期（同一持有期）的组合与基准数据：
- portfolio_weights / bench_weights: {资产: 期初权重}，各自 Σ≈1（现金作显式行业）。
- portfolio_returns / bench_returns: {资产: 该期收益率}。
- sectors: {资产: 行业标签}。

恒等式（逐行业求和 = 主动收益）：
- 配置 A_i = (Wp_i - Wb_i) × (Rb_i - Rb)
- 选股 S_i = Wb_i × (Rp_i - Rb_i)
- 交互 I_i = (Wp_i - Wb_i) × (Rp_i - Rb_i)
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import pandas as pd

_EPS = 1e-9


@dataclass
class BrinsonResult:
    table: pd.DataFrame          # 行业级行：Wp/Wb/Rp/Rb/allocation/selection/interaction/total
    total_active_return: float   # 组合收益 - 基准收益
    total_allocation: float
    total_selection: float
    total_interaction: float
    reconciliation: float        # Σ贡献 - 主动收益，应 ≈ 0


def brinson(portfolio_weights: dict[str, float],
            portfolio_returns: dict[str, float],
            bench_weights: dict[str, float],
            bench_returns: dict[str, float],
            sectors: dict[str, str],
            renormalize: bool = False) -> BrinsonResult:
    """单期 Brinson-BHB 归因。

    Args:
        renormalize: 权重和≠1 时是否静默归一（默认 False 报错——静默归一
                     会把现金暴露错误摊入各行业，掩盖仓位偏差）。
    """
    # 权重完整性校验（现金须显式建模为行业）
    for name, w in (("portfolio", portfolio_weights), ("bench", bench_weights)):
        s = sum(w.values())
        if abs(s - 1) > 1e-6:
            if renormalize:
                w = {k: v / s for k, v in w.items()}
                if name == "portfolio":
                    portfolio_weights = w
                else:
                    bench_weights = w
            else:
                raise ValueError(
                    f"{name} 权重和={s:.4f}≠1；含现金请显式加 cash 行业(收益0)，"
                    "或确认后传 renormalize=True")

    assets = sorted(set(portfolio_weights) | set(bench_weights))
    for a in assets:
        if a not in sectors:
            raise ValueError(f"资产 {a} 缺行业标签（sectors）")

    # 收益缺口：权重非零但收益缺失 → 报错（静默置 0 会虚假贡献）
    for name, weights, returns in (
            ("portfolio", portfolio_weights, portfolio_returns),
            ("bench", bench_weights, bench_returns)):
        for a, w in weights.items():
            if abs(w) > _EPS and a not in returns:
                raise ValueError(f"{name} 中 {a} 权重 {w} 但缺收益率")

    # 行业聚合
    inds = sorted({sectors[a] for a in assets})
    rows = []
    total_rp = sum(portfolio_weights.get(a, 0) * portfolio_returns.get(a, 0)
                   for a in assets)
    total_rb = sum(bench_weights.get(a, 0) * bench_returns.get(a, 0)
                   for a in assets)

    for ind in inds:
        a_in = [a for a in assets if sectors[a] == ind]
        wp = sum(portfolio_weights.get(a, 0) for a in a_in)
        wb = sum(bench_weights.get(a, 0) for a in a_in)
        rp = (sum(portfolio_weights.get(a, 0) * portfolio_returns.get(a, 0)
                  for a in a_in) / wp) if wp > _EPS else 0.0
        rb = (sum(bench_weights.get(a, 0) * bench_returns.get(a, 0)
                  for a in a_in) / wb) if wb > _EPS else 0.0
        alloc = (wp - wb) * (rb - total_rb)
        selec = wb * (rp - rb)
        inter = (wp - wb) * (rp - rb)
        rows.append({"industry": ind, "Wp": wp, "Wb": wb, "Rp": rp, "Rb": rb,
                     "allocation": alloc, "selection": selec,
                     "interaction": inter, "total": alloc + selec + inter})

    table = pd.DataFrame(rows).set_index("industry")
    active = total_rp - total_rb
    sum_alloc = float(table["allocation"].sum())
    sum_selec = float(table["selection"].sum())
    sum_inter = float(table["interaction"].sum())
    recon = sum_alloc + sum_selec + sum_inter - active
    if abs(recon) > 1e-6:
        raise ArithmeticError(f"Brinson 恒等式失衡：Σ贡献-主动收益={recon:.2e}")
    return BrinsonResult(table=table, total_active_return=active,
                          total_allocation=sum_alloc,
                          total_selection=sum_selec,
                          total_interaction=sum_inter,
                          reconciliation=recon)
