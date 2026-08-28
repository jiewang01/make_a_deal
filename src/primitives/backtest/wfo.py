"""WFO（Walk-Forward Optimization）滚动前进验证：三层验证第一层。

流程：滚动 [训练窗 | embargo | 测试窗]，每折：
1. 在训练窗上网格搜索最优参数（只看训练数据）；
2. 最优参数在紧随其后的测试窗（样本外）评估；
3. 汇总 OOS 表现 + IS/OOS 衰减比（过拟合警报）。

防参数泄漏（硬约束）：
- 训练窗与测试窗之间强制 embargo 间隔（默认 5 bar，防指标窗口跨越）；
- 每折参数只用该折训练数据选出（不用未来折的表现回头选参）；
- fold 切片生成器断言 train_end + embargo <= test_start。

过拟合判据（v0.6.0 A2 修复后）：
- decay = OOS/IS < 0.5 → 警报（样本外衰减过半）；
- OOS 加权均值 < 0 → 警报（样本外实亏，无论 decay 多少）。
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import warnings
import pandas as pd

_EPS = 1e-9


@dataclass
class FoldResult:
    fold: int
    train_start: int
    train_end: int        # 训练窗末索引（含）
    test_start: int
    test_end: int         # 测试窗末索引（含）
    best_params: dict
    is_score: float       # 训练窗最优分数
    oos_score: float      # 测试窗分数（样本外）
    n_candidates: int
    n_nan_candidates: int  # 训练窗上无法评估（策略无信号等）的参数数
    n_eval_errors: int = 0  # 修复 A1：评估抛异常的候选数（实现 bug，非不可评估）


@dataclass
class WFOResult:
    folds: list[FoldResult]
    oos_mean: float          # 折均 OOS 分数（按测试窗长度加权）
    oos_weighted: float
    is_mean: float
    decay_ratio: float | None  # OOS/IS（<0.5 视为过拟合警报）
    overfit_warning: bool


def generate_folds(n: int, train_len: int, test_len: int, embargo: int = 5):
    """生成 (train_slice, test_slice) 索引对，防重叠硬校验。

    Yields:
        (range(train_start, train_end+1), range(test_start, test_end+1))
    """
    if train_len < 1 or test_len < 1:
        raise ValueError("train_len/test_len 须 >= 1")
    if embargo < 0:
        raise ValueError("embargo 须 >= 0")
    fold = 0
    test_start = train_len + embargo
    while test_start + test_len <= n:
        train = range(test_start - embargo - train_len, test_start - embargo)
        # 硬校验：训练窗末 + embargo <= 测试窗首（防参数泄漏）
        assert train[-1] + embargo < test_start, "WFO fold 泄漏：训练窗与测试窗重叠"
        test = range(test_start, test_start + test_len)
        yield fold, train, test
        fold += 1
        test_start += test_len


def run_wfo(data: pd.DataFrame, param_grid: list[dict],
            evaluate, train_len: int, test_len: int,
            embargo: int = 5) -> WFOResult:
    """滚动前进优化。

    Args:
        data: 全样本行情/因子数据（index 升序）。
        param_grid: 候选参数组合列表。
        evaluate: callable(params, df_segment) -> float（分数，越大越好；
                  无法评估返回 NaN，如测试窗过短策略无信号）。
    Returns:
        WFOResult：各折明细 + 汇总（OOS 按测试窗长度加权平均）+ 过拟合警报。
    """
    if not param_grid:
        raise ValueError("param_grid 为空")
    data = data.sort_index()
    n = len(data)
    folds: list[FoldResult] = []
    for fold, train, test in generate_folds(n, train_len, test_len, embargo):
        train_df = data.iloc[train.start:train.stop]
        # 网格搜索：只看训练窗
        scores: list[float] = []
        n_eval_errors = 0
        for params in param_grid:
            try:
                scores.append(float(evaluate(params, train_df)))
            except Exception as exc:
                # 修复 A1：实现 bug 不得静默吞掉——告警 + 计数，与"不可评估(NaN)"区分
                warnings.warn(f"折 {fold} 参数 {params} 评估抛异常: "
                              f"{type(exc).__name__}: {exc}", stacklevel=2)
                n_eval_errors += 1
                scores.append(float("nan"))
        n_nan = sum(1 for s in scores if math.isnan(s))
        valid = [(s, i) for i, s in enumerate(scores) if not math.isnan(s)]
        if not valid:
            # 全部候选不可评估（训练窗过短）：该折记 NaN，不用默认参数伪装
            folds.append(FoldResult(fold, train.start, train[-1],
                                    test.start, test[-1], {}, float("nan"),
                                    float("nan"), len(param_grid), n_nan,
                                    n_eval_errors))
            continue
        best_s, best_i = max(valid)  # NaN 已剔除；并列取首个（稳定）
        best_params = param_grid[best_i]
        # 样本外评估：最优参数在紧随其后的测试窗
        test_df = data.iloc[test.start:test.stop]
        oos = float(evaluate(best_params, test_df))
        folds.append(FoldResult(fold, train.start, train[-1],
                                test.start, test[-1], best_params, best_s,
                                oos, len(param_grid), n_nan, n_eval_errors))

    valid_folds = [f for f in folds if not math.isnan(f.oos_score)]
    if not valid_folds:
        raise RuntimeError("WFO 无可评估折（样本过短或全部 OOS 为 NaN）")
    # 折均 OOS：按测试窗长度加权（不等长折等权会偏置短折）——测试窗等长
    # 时退化为算术平均；显式加权保证一般性
    total_len = sum(f.test_end - f.test_start + 1 for f in valid_folds)
    oos_weighted = sum(f.oos_score * (f.test_end - f.test_start + 1)
                       for f in valid_folds) / total_len
    oos_mean = sum(f.oos_score for f in valid_folds) / len(valid_folds)
    is_mean = sum(f.is_score for f in valid_folds) / len(valid_folds)
    decay = (oos_weighted / is_mean) if is_mean > _EPS else None
    # 修复 A2：OOS 实亏必警报（decay 无法捕捉 IS/OOS 同负的场景）
    overfit = (decay is not None and decay < 0.5) or (oos_weighted < 0)
    return WFOResult(folds=folds, oos_mean=oos_mean,
                     oos_weighted=oos_weighted, is_mean=is_mean,
                     decay_ratio=decay, overfit_warning=overfit)
