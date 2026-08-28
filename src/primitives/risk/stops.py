"""止损链：百分比 / ATR / 追踪止损，含跳空缺口规则。

与回测引擎一致的时序约定：
- t 日收盘后用截至 t 的数据判定触发；实际退出在 t+1 开盘（由调用方/引擎执行）。
- 跳空缺口规则（A 真实成本）：若 t+1 开盘已劣于止损价，成交价取开盘（跳空损失
  不可用止损价"假装"成交）；否则取止损价。本模块给出建议退出价。

防攻击面设计：
- 止损跳空：exit_price = max(stop_price, next_open)（多头），缺口损失显式化。
- ATR 除零：一字板（high==low）ATR=0 → 退化为不触发（窗口无效），显式告警。
- 前视：判定只用截至 t 的 OHLC 与 next_open（成交时点已知价）。
"""
from __future__ import annotations
from dataclasses import dataclass
import warnings
import pandas as pd


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """平均真实波幅 ATR(n)。TR = max(h-l, |h-pc|, |l-pc|)。"""
    h, l, pc = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([
        h - l,
        (h - pc).abs(),
        (l - pc).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


@dataclass
class StopEvent:
    """止损触发事件。"""
    date: pd.Timestamp          # 触发日（信号日，t）
    kind: str                   # pct / atr / trailing
    stop_price: float           # 止损价位（判定基准）
    exit_price: float           # 建议成交价（跳空缺口规则后）
    reason: str


class StopChain:
    """多止损组合：任一触发即离场。

    Args:
        entry_price: 入口成交价（含滑点）。
        pct: 百分比止损（如 0.08 = 跌 8% 离场），None 禁用。
        atr_n / atr_mult: ATR 止损（stop = entry - mult×ATR(entry时点)），None 禁用。
        trail: 追踪止损（峰值回撤比例，如 0.10），None 禁用。
    """

    def __init__(self, entry_price: float, *,
                 pct: float | None = 0.08,
                 atr_n: int | None = 14, atr_mult: float | None = 2.0,
                 trail: float | None = 0.10,
                 entry_atr: float | None = None):
        # 修复 A4：止损参数语义校验（比例须在 (0,1)）
        for name, v in (("pct", pct), ("trail", trail)):
            if v is not None and not (0 < v < 1):
                raise ValueError(f"止损参数 {name}={v} 须在 (0,1) 开区间")
        if atr_n is not None and atr_n < 1:
            raise ValueError(f"atr_n={atr_n} 须 >= 1")
        if atr_mult is not None and atr_mult <= 0:
            raise ValueError(f"atr_mult={atr_mult} 须 > 0")
        self.entry_price = float(entry_price)
        self.pct = pct
        self.trail = trail
        self.atr_n = atr_n
        self.atr_mult = atr_mult
        # ATR 止损价：入口时点 ATR 固定（entry_atr 由调用方在入口日计算传入）
        self.atr_stop: float | None = None
        if atr_n and atr_mult is not None and entry_atr is not None:
            if entry_atr <= 0:
                warnings.warn(f"入口 ATR={entry_atr} 无效（一字板/窗口不足），ATR 止损禁用",
                              stacklevel=2)
            else:
                self.atr_stop = self.entry_price - atr_mult * entry_atr
        self.peak_close: float = self.entry_price

    def update(self, bar: pd.Series) -> None:
        """t 日收盘后更新峰值（追踪止损用）。"""
        c = float(bar["close"])
        if c > self.peak_close:
            self.peak_close = c

    def check(self, bar: pd.Series, next_open: float | None = None,
              is_entry_day: bool = False) -> StopEvent | None:
        """t 日 K 线判定止损。

        修复 A1：is_entry_day=True（成交当日）豁免——止损基准（entry/peak）
        当日才成立，当日 low 击穿属"未成交就止损"的伪触发（日内逻辑混入日线）。

        多头口径：当日最低价击穿止损价即触发；退出价按跳空缺口规则：
        - next_open 未知（离线判定）：取 stop_price（保守估计由调用方修正）；
        - next_open 已知：min(stop_price, next_open)——次日开盘已劣于止损价时，
          只能以更差的开盘价成交（跳空损失显式化，不得假装按止损价成交）。
        """
        if is_entry_day:
            return None
        low = float(bar["low"])
        date = bar.name if hasattr(bar, "name") else None
        stop: float | None = None
        kind = reason = ""

        if self.pct is not None:
            s = self.entry_price * (1 - self.pct)
            if low <= s and (stop is None or s < stop):
                stop, kind, reason = s, "pct", f"跌破入口 {self.pct:.0%}"
        if self.atr_stop is not None:
            s = self.atr_stop
            if low <= s and (stop is None or s < stop):
                stop, kind, reason = s, "atr", f"跌破 ATR 止损价 {s:.2f}"
        if self.trail is not None:
            s = self.peak_close * (1 - self.trail)
            if low <= s and (stop is None or s < stop):
                stop, kind, reason = s, "trailing", \
                    f"自峰值 {self.peak_close:.2f} 回撤 {self.trail:.0%}"

        if stop is None:
            return None
        exit_price = stop if next_open is None else min(stop, float(next_open))
        return StopEvent(date=date, kind=kind, stop_price=stop,
                         exit_price=exit_price, reason=reason)
