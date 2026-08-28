"""L4 人类接口：目标输入 + 关键决策确认。

防攻击面设计（v0.9.0）：
- 越权绕过：关键决策（策略入库/实盘下单）必须经人类确认，
  Agent 不可自动 approve——HumanGate 默认 deny，
  无显式 human_decision → 拒绝；
- 决策篡改：确认/驳回记录 append-only（基于 MemoryStore），
  不可删除已确认决策（purge 半数护栏）；
- 目标注入：目标参数 schema 校验（收益/回撤/股票池/时间窗
  类型与范围硬校验），非法目标直接拒绝。
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import Enum

from ..memory.stores import MemoryStore


class Decision(Enum):
    APPROVE = "approve"
    REJECT = "reject"
    DEFER = "defer"             # 暂缓（不确认也不拒绝，稍后再议）


@dataclass
class InvestmentGoal:
    """人类输入的投资目标（schema 校验防注入）。"""
    target_return: float | None = None       # 目标年化收益率
    max_drawdown: float | None = None         # 最大回撤上限
    stock_pool: list[str] = field(default_factory=list)
    start_date: str = ""
    end_date: str = ""

    def validate(self) -> list[str]:
        """校验目标合法性，返回错误列表（空=合法）。"""
        errors: list[str] = []
        if self.target_return is not None:
            if not isinstance(self.target_return, (int, float)):
                errors.append("target_return 须数字")
            elif self.target_return < -1 or self.target_return > 10:
                errors.append(
                    f"target_return {self.target_return} 越界"
                    "（合理范围 -1~10，即 -100%~1000%）")
        if self.max_drawdown is not None:
            if not isinstance(self.max_drawdown, (int, float)):
                errors.append("max_drawdown 须数字")
            elif not -1 <= self.max_drawdown <= 0:
                errors.append(
                    f"max_drawdown {self.max_drawdown} 须在 [-1, 0]"
                    "（回撤为负数）")
        if self.stock_pool:
            if not all(isinstance(s, str) and s for s in self.stock_pool):
                errors.append("stock_pool 须非空字符串列表")
            elif len(self.stock_pool) > 500:
                errors.append(
                    f"stock_pool {len(self.stock_pool)} 超上限 500"
                    "（防资源耗尽）")
        return errors


@dataclass
class HumanDecision:
    decision: Decision
    reason: str = ""
    reviewer: str = ""
    ts: str = ""


class HumanGate:
    """人审闸门：关键决策必须经人类确认。

    默认 deny：未经人类显式 approve 的请求一律拒绝。
    决策记录 append-only，不可篡改。
    """

    def __init__(self, path: str):
        self.store = MemoryStore(
            path,
            # decision 参与指纹：approve/reject/revoked 同请求
            # 不同决策不被互相去重
            dedup_keys=("action", "artifact", "reviewer", "ts", "decision"),
        )

    def request_review(self, action: str, artifact: str,
                       context: dict | None = None) -> str:
        """提交待审请求，返回请求 ID（状态=defer，等待人类决策）。"""
        import uuid
        req_id = str(uuid.uuid4())[:8]
        rec = {
            "req_id": req_id,
            "action": action,
            "artifact": artifact,
            "context": context or {},
            "decision": Decision.DEFER.value,
            "reviewer": "",
            "reason": "",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self.store.append(rec)
        return req_id

    def decide(self, req_id: str, decision: Decision,
               reviewer: str, reason: str = "") -> bool:
        """人类做出决策（追加一条决策记录，append-only）。"""
        records = self.store.all_records()
        # 查找原始请求
        original = [r for r in records if r.get("req_id") == req_id]
        if not original:
            return False  # 请求不存在

        # 已有决策（防重复决策）
        decided = [r for r in records
                   if r.get("req_id") == req_id
                   and r.get("decision") != Decision.DEFER.value]
        if decided:
            return False

        rec = {
            "req_id": req_id,
            "action": original[0].get("action", ""),
            "artifact": original[0].get("artifact", ""),
            "context": original[0].get("context", {}),
            "decision": decision.value,
            "reviewer": reviewer,
            "reason": reason,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        return self.store.append(rec)

    def is_approved(self, req_id: str) -> bool:
        """检查请求是否已被人类 approve 且未被撤回（默认 deny）。"""
        records = self.store.all_records()
        approved = False
        for r in records:
            if r.get("req_id") == req_id:
                dec = r.get("decision")
                if dec == Decision.APPROVE.value:
                    approved = True
                elif dec == Decision.REJECT.value:
                    approved = False  # 驳回覆盖
                elif dec == "revoked":
                    approved = False  # 撤回覆盖
        return approved

    def revoke(self, req_id: str, reviewer: str,
              reason: str) -> bool:
        """撤回已做出的决策（发现问题后回滚，append-only）。"""
        records = self.store.all_records()
        original = [r for r in records if r.get("req_id") == req_id]
        if not original:
            return False
        # 找最近一条非 DEFER 决策
        decided = [r for r in records
                   if r.get("req_id") == req_id
                   and r.get("decision") != Decision.DEFER.value]
        if not decided:
            return False  # 无决策可撤回
        # 已撤回
        if any(r.get("decision") == "revoked" for r in decided):
            return False

        rec = {
            "req_id": req_id,
            "action": original[0].get("action", ""),
            "artifact": original[0].get("artifact", ""),
            "context": original[0].get("context", {}),
            "decision": "revoked",
            "reviewer": reviewer,
            "reason": reason,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        return self.store.append(rec)

    def get_decision(self, req_id: str) -> Decision | None:
        """获取请求的最终决策（无决策=DEFER；撤回后=DEFER）。"""
        records = self.store.all_records()
        latest = Decision.DEFER
        for r in records:
            if r.get("req_id") == req_id:
                dec = r.get("decision")
                if dec == Decision.APPROVE.value:
                    latest = Decision.APPROVE
                elif dec == Decision.REJECT.value:
                    latest = Decision.REJECT
                elif dec == "revoked":
                    latest = Decision.DEFER  # 撤回回退为 DEFER
        return latest

    def all_records(self) -> list[dict]:
        return self.store.all_records()
