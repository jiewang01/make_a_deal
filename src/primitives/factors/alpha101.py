"""Alpha101 因子子集（WorldQuant《101 Formulaic Alphas》，仅选日线 OHLCV 可算的公式）。

注册表模式：@register_factor("alpha006") 装饰器注册，compute_all 一次算全部。
横截面 rank 属于组合层（panel 数据），本模块输出单标的时序值；
横截面处理见 cleaner.cs_rank。

仅实现子集（v0.4.0）：alpha006 / alpha012 / alpha013 / alpha044。
"""
from __future__ import annotations
import pandas as pd

from .operators import delay, delta, sign, ts_corr, ts_cov, ts_rank

FACTOR_REGISTRY: dict[str, callable] = {}


def register_factor(name: str):
    """因子注册装饰器。"""
    def deco(fn):
        FACTOR_REGISTRY[name] = fn
        return fn
    return deco


# alpha(6): -1 * correlation(open, volume, 10)
@register_factor("alpha006")
def alpha006(df: pd.DataFrame) -> pd.Series:
    return -ts_corr(df["open"], df["volume"], 10)


# alpha(12): sign(delta(volume, 1)) * (-1 * delta(close, 1))
@register_factor("alpha012")
def alpha012(df: pd.DataFrame) -> pd.Series:
    return sign(delta(df["volume"], 1)) * (-delta(df["close"], 1))


# alpha(13): -1 * rank(cov(rank(close), rank(volume), 5))
# 单标的时序版：rank 取滚动百分位 rank（横截面 rank 在 panel 层做）
@register_factor("alpha013")
def alpha013(df: pd.DataFrame) -> pd.Series:
    return -ts_cov(ts_rank(df["close"], 5), ts_rank(df["volume"], 5), 5)


# alpha(44): -1 * correlation(high, rank(volume), 5)
@register_factor("alpha044")
def alpha044(df: pd.DataFrame) -> pd.Series:
    return -ts_corr(df["high"], ts_rank(df["volume"], 5), 5)


def compute(name: str, df: pd.DataFrame) -> pd.Series:
    """按名计算单个因子。"""
    if name not in FACTOR_REGISTRY:
        raise KeyError(f"因子 '{name}' 未注册，可选: {sorted(FACTOR_REGISTRY)}")
    return FACTOR_REGISTRY[name](df)


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """计算全部已注册因子，返回 DataFrame(index=date, columns=因子名)。"""
    out = {}
    for name, fn in FACTOR_REGISTRY.items():
        s = fn(df)
        s.name = name
        out[name] = s
    return pd.DataFrame(out)
