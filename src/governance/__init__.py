"""L4 治理层（Governance）：人机共审 · 沙箱隔离 · 审计卡点。

子模块：
- sandbox: 子进程隔离执行（禁网·禁写·资源限制·import 白名单）
- audit: 策略/因子入库前人审核（append-only 审计轨迹）
- human_interface: 人类监督接口（目标输入 + 关键决策确认）
"""
from .sandbox import Sandbox, SandboxConfig, SandboxResult
from .audit import AuditTrail, AuditStatus, AuditRecord
from .human_interface import (
    HumanGate, HumanDecision, InvestmentGoal, Decision,
)

__all__ = [
    "Sandbox", "SandboxConfig", "SandboxResult",
    "AuditTrail", "AuditStatus", "AuditRecord",
    "HumanGate", "HumanDecision", "InvestmentGoal", "Decision",
]
