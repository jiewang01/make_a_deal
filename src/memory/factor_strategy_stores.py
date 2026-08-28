"""因子 / 策略记忆库：去重 + 相似查重（生成新因子/策略前先查）。

FactorStore：name + formula 指纹去重；公式 token 相似度查重。
StrategyStore：name + rules 指纹去重；规则文本相似度查重。
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from .stores import MemoryStore


@dataclass
class DuplicationCheck:
    exact_dup: bool                  # 完全同指纹（同物重复）
    similar: list[tuple[float, dict]]  # 相似但非全同（需人工/Agent 复核）


def _merge_similar(similar: list[tuple[float, dict]]) \
        -> list[tuple[float, dict]]:
    """同名命中与相似度命中可能指向同一条记录 → 按记录身份去重取最高分。"""
    merged: dict[str, tuple[float, dict]] = {}
    for score, rec in similar:
        key = json.dumps(rec, ensure_ascii=False, sort_keys=True)
        if key not in merged or score > merged[key][0]:
            merged[key] = (score, rec)
    return sorted(merged.values(), key=lambda x: -x[0])


class FactorStore:
    """因子库：公式注册前查重（同名/同公式/相似公式）。"""

    def __init__(self, path: str):
        self.store = MemoryStore(path, dedup_keys=("name", "formula"))

    def register(self, name: str, formula: str, *, desc: str = "",
                 version: str = "") -> bool:
        """登记因子；同名同公式已存在 → False（防重复注册污染）。"""
        return self.store.append({
            "name": name, "formula": formula, "desc": desc,
            "version": version, "type": "factor",
        })

    def check_before_register(self, name: str, formula: str,
                              threshold: float = 0.7) -> DuplicationCheck:
        """注册前查重：同名或公式相似度过高的已有因子。"""
        records = self.store.all_records()
        exact = any(r.get("name") == name and r.get("formula") == formula
                    for r in records)
        if exact:
            return DuplicationCheck(True, [])
        similar: list[tuple[float, dict]] = []
        if name:
            for r in records:
                if r.get("name") == name:
                    similar.append((1.0, r))
        similar += self.store.search_similar(
            formula, threshold=threshold, text_fields=("formula",))
        return DuplicationCheck(False, _merge_similar(similar))

    def all_factors(self) -> list[dict]:
        return self.store.all_records()


class StrategyStore:
    """策略库：规则文本指纹去重 + 相似查重。"""

    def __init__(self, path: str):
        self.store = MemoryStore(path, dedup_keys=("name", "rules"))

    def register(self, name: str, rules: str, *, desc: str = "",
                 version: str = "") -> bool:
        return self.store.append({
            "name": name, "rules": rules, "desc": desc,
            "version": version, "type": "strategy",
        })

    def check_before_register(self, name: str, rules: str,
                              threshold: float = 0.7) -> DuplicationCheck:
        records = self.store.all_records()
        exact = any(r.get("name") == name and r.get("rules") == rules
                    for r in records)
        if exact:
            return DuplicationCheck(True, [])
        similar: list[tuple[float, dict]] = []
        if name:
            for r in records:
                if r.get("name") == name:
                    similar.append((1.0, r))
        similar += self.store.search_similar(
            rules, threshold=threshold, text_fields=("rules",))
        return DuplicationCheck(False, _merge_similar(similar))

    def all_strategies(self) -> list[dict]:
        return self.store.all_records()
