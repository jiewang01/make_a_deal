"""经验库：记录 Attacker 攻击打回的失败点，避免重蹈。

格式（JSONL，每行一条）：
    {"ts": "...", "version": "v0.2.0", "attack_point": "前视偏差",
     "desc": "...", "fix": "...", "status": "fixed|open"}

v0.8.0 重构：基于 MemoryStore 基座——原子写（fsync）+ 内容指纹去重
（同 version+attack_point+desc+fix 不重复入库，防攻击记录污染）。
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path

from ..stores import MemoryStore

STORE = Path(__file__).parent / "records.jsonl"

# 易变字段 ts/status 不参与指纹
_store = MemoryStore(
    str(STORE),
    dedup_keys=("version", "attack_point", "desc", "fix"),
)


def append(version: str, attack_point: str, desc: str, fix: str,
           status: str = "fixed") -> bool:
    """追加一条经验记录（内容重复 → False 拒绝）。"""
    return _store.append({
        "ts": datetime.now().isoformat(),
        "version": version,
        "attack_point": attack_point,
        "desc": desc,
        "fix": fix,
        "status": status,
    })


def all_records() -> list[dict]:
    """读取全部经验记录。"""
    return _store.all_records()


def search(keyword: str) -> list[dict]:
    """按关键词查经验库，生成新因子/策略前查重。"""
    return _store.search_keyword(keyword)


def purge(status: str):
    """清理指定 status 的经验记录（受 purge 半数护栏保护）。"""
    return _store.purge(lambda r: r.get("status") == status)
