"""经验库：记录 Attacker 攻击打回的失败点，避免重蹈。

格式（JSONL，每行一条）：
    {"ts": "...", "version": "v0.2.0", "attack_point": "前视偏差",
     "desc": "...", "fix": "...", "status": "fixed|open"}
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime

STORE = Path(__file__).parent / "records.jsonl"


def append(version: str, attack_point: str, desc: str, fix: str,
           status: str = "fixed") -> None:
    """追加一条经验记录。"""
    rec = {
        "ts": datetime.now().isoformat(),
        "version": version,
        "attack_point": attack_point,
        "desc": desc,
        "fix": fix,
        "status": status,
    }
    with open(STORE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def all_records() -> list[dict]:
    """读取全部经验记录。"""
    if not STORE.exists():
        return []
    return [
        json.loads(line)
        for line in STORE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def search(keyword: str) -> list[dict]:
    """按关键词查经验库，生成新因子/策略前查重。"""
    return [r for r in all_records() if keyword in r.get("desc", "") or
            keyword in r.get("attack_point", "")]
