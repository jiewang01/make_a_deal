"""L4 审计：策略/因子入库前人审核卡点。

防攻击面设计（v0.9.0）：
- 审核绕过：未经审核的 artifact（因子/策略）不能进入
  FactorStore/StrategyStore——register 前必须过审计，
  audit_pending → approved 才允许注册；
- 审计篡改：审计记录写入后不可变（append-only JSONL），
  不可删除已通过审核的记录（purge 半数护栏保护）；
- 审计缺证据：审核须附带回测/偏差检测结果，无证据即拒绝
  （防止"拍脑袋"通过）。
"""
from __future__ import annotations
import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum

from ..memory.stores import MemoryStore


class AuditStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"          # 审核通过后发现问题，撤回


@dataclass
class AuditRecord:
    artifact_type: str            # factor / strategy
    name: str
    content: str                  # formula / rules
    version: str = ""
    status: AuditStatus = AuditStatus.PENDING
    evidence: dict = field(default_factory=dict)   # 回测/偏差检测结果
    reviewer: str = ""            # 审核人（空=待审）
    review_ts: str = ""
    reject_reason: str = ""


class AuditTrail:
    """审计轨迹：append-only，不可篡改。

    审核记录存储为 JSONL（基于 MemoryStore 基座），
    指纹去重防重复提交（同 name+content 只有一条 pending）。
    """

    def __init__(self, path: str):
        self.store = MemoryStore(
            path,
            # status 参与指纹：同一 artifact 不同状态（pending/
            # approved/rejected/revoked）不被互相去重
            dedup_keys=("artifact_type", "name", "content", "version",
                        "status"),
        )

    def submit(self, artifact_type: str, name: str, content: str, *,
               version: str = "", evidence: dict | None = None) -> bool:
        """提交待审核 artifact（须附 evidence——无证据即拒绝）。"""
        if not evidence:
            raise ValueError(
                "审核提交须附带 evidence（回测/偏差检测结果），"
                "无证据的 artifact 拒绝审核——防拍脑袋通过")
        if artifact_type not in ("factor", "strategy"):
            raise ValueError(
                f"artifact_type 须 factor/strategy，得到 {artifact_type}")

        rec = {
            "artifact_type": artifact_type,
            "name": name,
            "content": content,
            "version": version,
            "status": AuditStatus.PENDING.value,
            "evidence": evidence,
            "reviewer": "",
            "review_ts": "",
            "reject_reason": "",
            "submit_ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        return self.store.append(rec)

    def approve(self, name: str, content: str, reviewer: str,
                version: str = "") -> bool:
        """审核通过：将 pending 记录标记为 approved。

        安全：不修改原记录（append-only 不可变），
        而是追加一条 approved 状态记录（同指纹去重防重复审核）。
        """
        records = self.store.all_records()
        # 检查是否已有 pending
        pending = [r for r in records
                   if r.get("name") == name
                   and r.get("content") == content
                   and r.get("version") == version
                   and r.get("status") == AuditStatus.PENDING.value]
        if not pending:
            return False  # 无 pending 记录，不可审核

        # 检查是否已 approved（防重复审核）
        already = [r for r in records
                   if r.get("name") == name
                   and r.get("content") == content
                   and r.get("version") == version
                   and r.get("status") in (
                       AuditStatus.APPROVED.value,
                       AuditStatus.REVOKED.value)]
        if already:
            return False

        rec = {
            "artifact_type": pending[0]["artifact_type"],
            "name": name,
            "content": content,
            "version": version,
            "status": AuditStatus.APPROVED.value,
            "evidence": pending[0].get("evidence", {}),
            "reviewer": reviewer,
            "review_ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "reject_reason": "",
            "submit_ts": pending[0].get("submit_ts", ""),
        }
        return self.store.append(rec)

    def reject(self, name: str, content: str, reviewer: str,
               reason: str, version: str = "") -> bool:
        """审核驳回：标记 rejected 并记录原因。"""
        records = self.store.all_records()
        pending = [r for r in records
                   if r.get("name") == name
                   and r.get("content") == content
                   and r.get("version") == version
                   and r.get("status") == AuditStatus.PENDING.value]
        if not pending:
            return False

        rec = {
            "artifact_type": pending[0]["artifact_type"],
            "name": name,
            "content": content,
            "version": version,
            "status": AuditStatus.REJECTED.value,
            "evidence": pending[0].get("evidence", {}),
            "reviewer": reviewer,
            "review_ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "reject_reason": reason,
            "submit_ts": pending[0].get("submit_ts", ""),
        }
        return self.store.append(rec)

    def revoke(self, name: str, content: str, reviewer: str,
               reason: str, version: str = "") -> bool:
        """撤回已通过的审核（发现问题后回滚）。"""
        records = self.store.all_records()
        approved = [r for r in records
                    if r.get("name") == name
                    and r.get("content") == content
                    and r.get("version") == version
                    and r.get("status") == AuditStatus.APPROVED.value]
        if not approved:
            return False

        rec = {
            "artifact_type": approved[0]["artifact_type"],
            "name": name,
            "content": content,
            "version": version,
            "status": AuditStatus.REVOKED.value,
            "evidence": approved[0].get("evidence", {}),
            "reviewer": reviewer,
            "review_ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "reject_reason": reason,
            "submit_ts": approved[0].get("submit_ts", ""),
        }
        return self.store.append(rec)

    def is_approved(self, name: str, content: str,
                    version: str = "") -> bool:
        """检查 artifact 是否已审核通过（且未被撤回）。"""
        records = self.store.all_records()
        has_approved = False
        for r in records:
            if (r.get("name") == name
                    and r.get("content") == content
                    and r.get("version") == version):
                status = r.get("status")
                if status == AuditStatus.APPROVED.value:
                    has_approved = True
                elif status == AuditStatus.REVOKED.value:
                    has_approved = False  # 撤回覆盖
        return has_approved

    def all_records(self) -> list[dict]:
        return self.store.all_records()
