"""市场择时过滤器：均线趋势择时（大盘/个股皆可）。

规则：收盘价 > MA(n) → 风险偏好 on（允许开新仓）；否则 off。
仅用截至 t 的数据，无前视。
"""
from __future__ import annotations
import pandas as pd


class MATiming:
    """均线趋势择时。

    Args:
        n: 均线周期（如 200 日牛熊线 / 20 日短趋势）。
        exit_on_off: off 时是否强制清仓（True）还是仅停止开新仓（False）。
    """

    def __init__(self, n: int = 200, exit_on_off: bool = False):
        if n < 2:
            raise ValueError("均线周期须 >= 2")
        self.n = n
        self.exit_on_off = exit_on_off

    def is_on(self, closes: pd.Series) -> bool:
        """截至 t 的收盘序列 → 趋势开关。窗口不足/空序列/NaN 时保守返回 False。"""
        # 修复 A3：空序列防御（rolling().iloc[-1] 会 IndexError）
        if closes is None or len(closes) < self.n:
            return False
        ma = float(closes.rolling(self.n).mean().iloc[-1])
        c = float(closes.iloc[-1])
        if pd.isna(ma) or pd.isna(c):
            return False
        return c > ma

    def should_exit(self, closes: pd.Series) -> bool:
        """是否应清仓：exit_on_off 且趋势 off。"""
        return self.exit_on_off and not self.is_on(closes)
