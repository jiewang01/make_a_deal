"""L2 回测引擎（事件驱动四阶段 + A 股规则完整建模）。

公开接口：
- BacktestEngine(df, strategy): 事件驱动回测（Market→Signal→Order→Fill）
- Strategy / Signal / MACross: 策略基类与均线交叉策略
- AshareFeeConfig: A 股费用模型（佣金/印花税/过户费/滑点）
- Position: lot 级持仓（T+1）
"""
from .ashare_rules import (
    AshareFeeConfig, limit_prices, is_limit_up, is_limit_down, round_to_lot,
)
from .strategy import Strategy, Signal, MACross, ScriptStrategy
from .engine import (
    BacktestEngine, BacktestResult, Fill, Reject, Position, Lot,
)

__all__ = [
    "AshareFeeConfig", "limit_prices", "is_limit_up", "is_limit_down",
    "round_to_lot", "Strategy", "Signal", "MACross", "ScriptStrategy",
    "BacktestEngine", "BacktestResult", "Fill", "Reject", "Position", "Lot",
]
