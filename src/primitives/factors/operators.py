"""时间序列算子（WorldQuant Alpha101 风格，单标的日线）。

约定：
- 所有算子只依赖截至 t 的历史（rolling/delay/delta 均向后看），无前视。
- 输出与输入等长，窗口不足处为 NaN。
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def delay(s: pd.Series, n: int) -> pd.Series:
    """s(t-n)。"""
    return s.shift(n)


def delta(s: pd.Series, n: int = 1) -> pd.Series:
    """s(t) - s(t-n)。"""
    return s.diff(n)


def sign(s: pd.Series) -> pd.Series:
    """符号函数，NaN 保持 NaN。"""
    return np.sign(s)


def ts_corr(a: pd.Series, b: pd.Series, n: int,
            min_periods: int | None = None) -> pd.Series:
    """滚动 n 日相关系数。"""
    return a.rolling(n, min_periods=min_periods or n).corr(b)


def ts_cov(a: pd.Series, b: pd.Series, n: int,
           min_periods: int | None = None) -> pd.Series:
    """滚动 n 日协方差。"""
    return a.rolling(n, min_periods=min_periods or n).cov(b)


def ts_std(s: pd.Series, n: int,
           min_periods: int | None = None) -> pd.Series:
    """滚动 n 日标准差。"""
    return s.rolling(n, min_periods=min_periods or n).std()


def ts_rank(s: pd.Series, n: int,
            min_periods: int | None = None) -> pd.Series:
    """滚动窗口内当前值的排名比例 (0,1]。"""
    return s.rolling(n, min_periods=min_periods or n).rank(pct=True)
