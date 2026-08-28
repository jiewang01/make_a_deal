"""L2 组合风控 + 择时：risk_gate（日线 5 层）+ stops（ATR/百分比/追踪）+ timing。

公开接口：
- apply_risk_gate / RiskConfig / GateResult: 权重风控闸门
- StopChain / StopEvent / atr: 止损链与 ATR
- MATiming: 均线趋势择时
"""
from .risk_gate import (
    RiskConfig, GateReport, GateResult, apply_risk_gate,
)
from .stops import StopChain, StopEvent, atr
from .timing import MATiming

__all__ = [
    "RiskConfig", "GateReport", "GateResult", "apply_risk_gate",
    "StopChain", "StopEvent", "atr", "MATiming",
]
