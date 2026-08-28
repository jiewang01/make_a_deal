"""A 股交易规则建模：费用 / 涨跌停 / 整手 / T+1 校验。

Attacker 攻击面预设防御：
- 费用漏算：佣金(双向万2.5最低5元) + 印花税(卖出单向千0.5) + 过户费(双向万0.1) + 滑点，全部显式建模，缺一不可。
- 涨跌停可成交：以 fill 前一交易日收盘为 prev_close 计算板价，开盘价触板则反向单不成交。
- 100 股整数倍：买卖数量向下取整到 100，不足 100 视为 0（不成交）。
- T+1：见 engine.Position.sellable_shares，按 lot 的 buy_date 严格判定。
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class AshareFeeConfig:
    """A 股费用模型（2023 后印花税减半为千 0.5，2022 过户费沪深统一双向万 0.1）。"""
    commission_rate: float = 0.00025      # 佣金费率（双向，含规费）
    commission_min: float = 5.0          # 单笔佣金最低 5 元
    stamp_tax_rate: float = 0.0005       # 印花税（卖出单向）
    transfer_fee_rate: float = 0.00001   # 过户费（双向，万 0.1）
    slippage_rate: float = 0.001         # 滑点（千 1，按成交价）

    def commission(self, turnover: float) -> float:
        """佣金：双向，最低 5 元。"""
        return max(turnover * self.commission_rate, self.commission_min)

    def stamp_tax(self, turnover: float, is_sell: bool) -> float:
        """印花税：仅卖出征收。"""
        return turnover * self.stamp_tax_rate if is_sell else 0.0

    def transfer_fee(self, turnover: float) -> float:
        """过户费：双向。"""
        return turnover * self.transfer_fee_rate

    def slippage_price(self, price: float, is_buy: bool) -> float:
        """滑点：买入价上浮，卖出价下浮。"""
        return price * (1 + self.slippage_rate) if is_buy else price * (1 - self.slippage_rate)


def limit_prices(prev_close: float, factor: float = 0.1) -> tuple[float, float]:
    """涨跌停价：以 prev_close 为基准，factor=0.1（ST=0.05）。

    交易所按 2 位小数四舍五入。
    """
    up = round(prev_close * (1 + factor), 2)
    down = round(prev_close * (1 - factor), 2)
    return up, down


def is_limit_up(open_price: float, prev_close: float, factor: float = 0.1) -> bool:
    """开盘价触及涨停价 → 买单不成交（封板买不进）。"""
    up, _ = limit_prices(prev_close, factor)
    return open_price >= up - 1e-9


def is_limit_down(open_price: float, prev_close: float, factor: float = 0.1) -> bool:
    """开盘价触及跌停价 → 卖单不成交（封板卖不出）。"""
    _, down = limit_prices(prev_close, factor)
    return open_price <= down + 1e-9


def round_to_lot(shares: float, lot: int = 100) -> int:
    """A 股最小交易单位 100 股，向下取整；不足 100 股返回 0。"""
    n = int(shares) // lot * lot
    return n
