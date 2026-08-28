"""事件驱动回测引擎：Market → Signal → Order → Fill 四阶段。

防攻击面设计：
- 前视偏差：t 日收盘后调用 strategy.on_bar(截至 t 的数据)，成交在 t+1 开盘；
  最后一根 bar 不生成信号（无次日可成交）。策略永远拿不到 fill 日数据。
- T+1：Lot 级持仓，sellable_shares 只统计 buy_date < fill_date 的 lot。
- 涨跌停：以信号日收盘为 prev_close，fill 日开盘触板则反向单不成交并记 Reject。
- 费用：佣金(双向,最低5元) + 印花税(卖出单向) + 过户费(双向) + 滑点，全部计入现金。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import math
import pandas as pd

from .ashare_rules import (
    AshareFeeConfig, is_limit_up, is_limit_down, round_to_lot, limit_prices,
)
from .strategy import Signal

_REQUIRED_COLS = {"open", "high", "low", "close", "volume"}


@dataclass
class Lot:
    """一笔买入持仓（T+1 判定单元）。"""
    shares: int
    buy_date: pd.Timestamp
    price: float  # 含滑点买入价
    fee_per_share: float = 0.0  # 买入费用均摊到每股（修复 A2：双边费用计入盈亏）


@dataclass
class Fill:
    """成交回报。"""
    date: pd.Timestamp
    code: str
    action: str  # buy / sell
    shares: int
    price: float  # 含滑点成交价
    commission: float
    stamp_tax: float
    transfer_fee: float
    signal_date: pd.Timestamp


@dataclass
class Reject:
    """拒单记录（涨跌停/资金不足/T+1 不可卖等）。"""
    date: pd.Timestamp
    action: str
    reason: str  # limit_up / limit_down / insufficient_cash / t_plus_1_or_empty / insufficient_shares
    signal_date: pd.Timestamp


class Position:
    """lot 级持仓，T+1 依据 buy_date 判定可卖。"""

    def __init__(self):
        self.lots: list[Lot] = []

    @property
    def total_shares(self) -> int:
        return sum(l.shares for l in self.lots)

    def add(self, shares: int, date: pd.Timestamp, price: float,
            buy_fee: float = 0.0) -> None:
        """buy_fee：该笔买入总费用（佣金+过户费），均摊入 lot。"""
        self.lots.append(Lot(shares, date, price, buy_fee / shares if shares else 0.0))

    def sellable_shares(self, date: pd.Timestamp) -> int:
        """T+1：仅 buy_date 严格早于 date 的 lot 可卖。"""
        return sum(l.shares for l in self.lots if l.buy_date < date)

    def reduce_fifo(self, shares: int) -> list[tuple[int, float]]:
        """FIFO 减仓，返回配对 [(股数, 含费成本价)]（price + fee_per_share）。"""
        out: list[tuple[int, float]] = []
        remaining = shares
        while remaining > 0 and self.lots:
            lot = self.lots[0]
            take = min(lot.shares, remaining)
            out.append((take, lot.price + lot.fee_per_share))
            lot.shares -= take
            remaining -= take
            if lot.shares == 0:
                self.lots.pop(0)
        return out


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    fills: list[Fill]
    rejects: list[Reject]
    metrics: dict


class BacktestEngine:
    """单标的日线事件驱动回测引擎。

    用法：
        eng = BacktestEngine(df, MACross(5, 20), cash=1_000_000, code="600519")
        result = eng.run()
    """

    def __init__(self, df: pd.DataFrame, strategy, *,
                 cash: float = 1_000_000.0, code: str = "",
                 fee: Optional[AshareFeeConfig] = None,
                 limit_factor: float = 0.1):
        missing = _REQUIRED_COLS - set(df.columns)
        if missing:
            raise ValueError(f"行情缺列: {sorted(missing)}，需 {_REQUIRED_COLS}")
        if len(df) < 3:
            raise ValueError("行情不足 3 根 bar，无法回测")
        self.df = df.sort_index()
        self.strategy = strategy
        self.cash = float(cash)
        self.init_cash = float(cash)
        self.code = code
        self.fee = fee or AshareFeeConfig()
        self.limit_factor = limit_factor
        self.position = Position()
        self.fills: list[Fill] = []
        self.rejects: list[Reject] = []
        self.realized_pnls: list[float] = []
        self._pending: Optional[tuple[Signal, int]] = None

    # ------------------------------------------------------------------ run
    def run(self) -> BacktestResult:
        df, n = self.df, len(self.df)
        equity: dict[pd.Timestamp, float] = {}
        for t in range(n):
            # ① Fill：昨日信号今日开盘撮合
            if self._pending is not None:
                sig, sig_t = self._pending
                self._pending = None
                self._execute(sig, sig_t, t)
            # ② Signal：t 日收盘生成信号（最后一日无次日成交，不生成）
            if t < n - 1:
                sig = self.strategy.on_bar(df.iloc[: t + 1])
                if sig is not None:
                    self._pending = (sig, t)
            # ③ Market：收盘 mark-to-market
            equity[df.index[t]] = self.cash + self.position.total_shares * float(df["close"].iloc[t])
        curve = pd.Series(equity).sort_index()
        return BacktestResult(curve, self.fills, self.rejects, self._metrics(curve))

    # --------------------------------------------------------------- execute
    def _execute(self, sig: Signal, sig_t: int, t: int) -> None:
        df = self.df
        bar = df.iloc[t]
        fill_date = df.index[t]
        signal_date = df.index[sig_t]
        prev_close = float(df["close"].iloc[sig_t])  # 信号日收盘，涨跌停判定基准
        open_price = float(bar["open"])
        if sig.action == "buy":
            self._fill_buy(sig, open_price, prev_close, fill_date, signal_date)
        elif sig.action in ("sell", "close"):
            self._fill_sell(sig, open_price, prev_close, fill_date, signal_date)
        else:
            raise ValueError(f"未知信号动作: {sig.action}")

    def _fill_buy(self, sig: Signal, open_price: float, prev_close: float,
                  fill_date: pd.Timestamp, signal_date: pd.Timestamp) -> None:
        # 涨跌停：开盘触涨停 → 买单不成交（封板买不进）
        if is_limit_up(open_price, prev_close, self.limit_factor):
            self.rejects.append(Reject(fill_date, "buy", "limit_up", signal_date))
            return
        # 修复 A1 滑点穿透涨跌停：含滑点价 clamp 至板内（交易所价格笼子）
        up, _ = limit_prices(prev_close, self.limit_factor)
        price = min(self.fee.slippage_price(open_price, is_buy=True), up)
        budget = self.cash * sig.size
        shares = round_to_lot(budget / price)
        # 逐手缩量直到现金可负担（费用随股数变化，须重算）
        while shares >= 100:
            turnover = shares * price
            cost = self.fee.commission(turnover) + self.fee.transfer_fee(turnover)
            if turnover + cost <= self.cash + 1e-9:
                break
            shares -= 100
        if shares < 100:
            self.rejects.append(Reject(fill_date, "buy", "insufficient_cash", signal_date))
            return
        turnover = shares * price
        commission = self.fee.commission(turnover)
        transfer = self.fee.transfer_fee(turnover)
        self.cash -= turnover + commission + transfer
        self.position.add(shares, fill_date, price, buy_fee=commission + transfer)
        self.fills.append(Fill(fill_date, self.code, "buy", shares, price,
                               commission, 0.0, transfer, signal_date))

    def _fill_sell(self, sig: Signal, open_price: float, prev_close: float,
                   fill_date: pd.Timestamp, signal_date: pd.Timestamp) -> None:
        # 涨跌停：开盘触跌停 → 卖单不成交（封板卖不出）
        if is_limit_down(open_price, prev_close, self.limit_factor):
            self.rejects.append(Reject(fill_date, "sell", "limit_down", signal_date))
            return
        sellable = self.position.sellable_shares(fill_date)  # T+1 约束
        if sellable < 100:
            self.rejects.append(Reject(fill_date, sig.action, "t_plus_1_or_empty", signal_date))
            return
        # 修复 A1 滑点穿透涨跌停：含滑点价 clamp 至板内（交易所价格笼子）
        _, down = limit_prices(prev_close, self.limit_factor)
        price = max(self.fee.slippage_price(open_price, is_buy=False), down)
        shares = round_to_lot(sellable * sig.size)
        if shares < 100:
            self.rejects.append(Reject(fill_date, sig.action, "insufficient_shares", signal_date))
            return
        turnover = shares * price
        commission = self.fee.commission(turnover)
        stamp = self.fee.stamp_tax(turnover, is_sell=True)
        transfer = self.fee.transfer_fee(turnover)
        pairs = self.position.reduce_fifo(shares)
        # 已实现盈亏：双边费用口径（买入费已摊入 lot 成本价，卖出费在此扣除）
        gross = sum((price - bp) * s for s, bp in pairs)
        self.realized_pnls.append(gross - commission - stamp - transfer)
        self.cash += turnover - commission - stamp - transfer
        self.fills.append(Fill(fill_date, self.code, "sell", shares, price,
                               commission, stamp, transfer, signal_date))

    # -------------------------------------------------------------- metrics
    def _metrics(self, curve: pd.Series) -> dict:
        total = float(curve.iloc[-1] / curve.iloc[0] - 1)
        n = len(curve)
        dd = float((curve / curve.cummax() - 1).min())
        ret = curve.pct_change().dropna()
        # 修复 A4：样本不足一个季度(63 交易日)时年化/夏普无统计意义，置 None 防误导
        if n >= 63:
            annual = float((1 + total) ** (252 / n) - 1)
            sharpe = (float(ret.mean() / ret.std() * math.sqrt(252))
                      if len(ret) > 1 and float(ret.std()) > 0 else 0.0)
        else:
            annual = None
            sharpe = None
        win_rate = (sum(1 for p in self.realized_pnls if p > 0) / len(self.realized_pnls)
                    if self.realized_pnls else None)
        # 修复 A3：期末未平仓敞口显式暴露，防止浮盈亏被解读遗漏
        open_shares = self.position.total_shares
        last_close = float(self.df["close"].iloc[-1])
        return {
            "total_return": total,
            "annual_return": annual,
            "max_drawdown": dd,
            "sharpe": sharpe,
            "n_fills": len(self.fills),
            "n_rejects": len(self.rejects),
            "n_round_trips": len(self.realized_pnls),
            "win_rate": win_rate,
            "init_cash": self.init_cash,
            "final_equity": float(curve.iloc[-1]),
            "open_position_shares": open_shares,
            "open_position_value": open_shares * last_close,
        }
