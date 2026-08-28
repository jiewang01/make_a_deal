"""子区间稳定性分析：三层验证第二层。

把权益曲线切成 K 段，逐段收益 + 离散度 + 最差段，识别"靠单段行情"的策略。
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class StabilityResult:
    n_segments: int
    seg_returns: list[float]     # 各段区间收益
    mean: float
    std: float
    worst: float                 # 最差段
    best: float
    positive_ratio: float        # 盈利段占比
    stable: bool                 # 稳定性判据（见 _judge）
    reason: str


def segment_stability(equity: pd.Series, k: int = 5) -> StabilityResult:
    """权益曲线 K 段切分稳定性。

    判据（全部满足才 stable）：
    - 无段收益 < -20%（单段深度崩塌）；
    - 盈利段占比 >= 0.5；
    - 段收益离散度 std < |mean| × 2（均值不显著时另判）。

    修复 A3/A4：
    - 等长切段：len % k ≠ 0 时丢弃开头余数（len//k 点），保证段间可比；
    - 每段须 >= 2 个点（k=len 时每段 1 点恒 0 收益 → 伪稳定），即 len >= 2k。
    """
    if not isinstance(equity, pd.Series) or k < 2:
        raise ValueError("k 须 >= 2")
    eq = equity.sort_index()
    if len(eq) < 2 * k:
        raise ValueError(f"权益曲线长度须 >= 2×k={2 * k}（每段至少 2 个点）")
    if eq.isna().any():
        raise ValueError("权益曲线含 NaN")
    if (eq <= 0).any():
        raise ValueError("权益曲线含非正值")
    # 修复 A3：等长切段，丢弃开头余数（含 burn-in 语义）
    seg_len = len(eq) // k
    vals = eq.values[len(eq) - seg_len * k:]
    segs = [vals[i * seg_len:(i + 1) * seg_len] for i in range(k)]
    # 各段收益：段末/段首-1；段首用上一段末尾（连续无重叠）
    rets: list[float] = []
    prev = None
    for seg in segs:
        base = seg[0] if prev is None else prev
        rets.append(float(seg[-1] / base - 1))
        prev = seg[-1]
    arr = np.array(rets)
    mean, std = float(arr.mean()), float(arr.std(ddof=0))
    worst, best = float(arr.min()), float(arr.max())
    pos_ratio = float((arr > 0).mean())
    flags = []
    if worst < -0.20:
        flags.append(f"最差段 {worst:.1%} 深度崩塌")
    if pos_ratio < 0.5:
        flags.append(f"盈利段占比 {pos_ratio:.0%} < 50%")
    if abs(mean) > 1e-9 and std > abs(mean) * 2:
        flags.append(f"离散度 std={std:.3f} > 2×|mean|={abs(mean):.3f}")
    if abs(mean) <= 1e-9 and std > 0.05:
        flags.append("均值≈0 但波动显著")
    stable = not flags
    return StabilityResult(k, rets, mean, std, worst, best, pos_ratio,
                           stable, "; ".join(flags) if flags else "稳定")
