"""策略基类与内置策略（均线交叉）。

防前视约定：on_bar 只接收截至信号日 t 的行情（df.iloc[:t+1]），
策略不得访问 t 日之后的任何数据；成交由引擎在 t+1 开盘执行。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd


@dataclass
class Signal:
    """交易信号。

    action: "buy" / "sell" / "close"
    size: buy 为动用现金比例(0,1]；sell/close 为卖出可卖持仓的比例(0,1]
    """
    action: str
    size: float = 1.0


class Strategy:
    """策略基类。子类实现 on_bar，返回 Signal 或 None。"""

    def on_bar(self, df: pd.DataFrame) -> Optional[Signal]:
        raise NotImplementedError


class MACross(Strategy):
    """均线交叉：快线上穿慢线（金叉）买入，下穿（死叉）全仓卖出。"""

    def __init__(self, fast: int = 5, slow: int = 20):
        if fast >= slow:
            raise ValueError("fast 必须小于 slow")
        self.fast, self.slow = fast, slow

    def on_bar(self, df: pd.DataFrame) -> Optional[Signal]:
        # 需要慢线窗口满 + 前一日均线值（判交叉需 t 与 t-1 两点）
        if len(df) < self.slow + 2:
            return None
        close = df["close"]
        f = close.rolling(self.fast).mean()
        s = close.rolling(self.slow).mean()
        if pd.isna(f.iloc[-2]) or pd.isna(s.iloc[-2]):
            return None
        golden = f.iloc[-2] <= s.iloc[-2] and f.iloc[-1] > s.iloc[-1]
        dead = f.iloc[-2] >= s.iloc[-2] and f.iloc[-1] < s.iloc[-1]
        if golden:
            return Signal("buy", 0.95)
        if dead:
            return Signal("sell", 1.0)
        return None


class ScriptStrategy(Strategy):
    """脚本策略（测试用）：{bar索引: Signal}，按 bar 序号发信号。"""

    def __init__(self, plan: dict[int, Signal]):
        self.plan = plan

    def on_bar(self, df: pd.DataFrame) -> Optional[Signal]:
        return self.plan.get(len(df) - 1)
