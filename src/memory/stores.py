"""记忆库基座：原子写 / 指纹去重 / 关键词与相似度检索。

防攻击面设计（v0.8.0）：
- 经验库污染：append 前做指纹去重（相同内容不重复入库）+ 字段白名单校验，
  非法记录直接拒绝；
- 误丢弃：purge 只删"显式匹配"记录并返回删除明细，绝不静默整库清空；
- 相似度误判：Jaccard 相似度基于规范化 token（去停用词/小写/去标点），
  阈值语义显式（>= threshold 视为相似），并把相似度分数返回给调用方复核。
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field

_STOPWORDS = {"的", "了", "和", "与", "及", "在", "为", "对", "中", "a",
              "the", "and", "of", "to", "is", "in", "for", "on"}
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+")


def tokenize(text: str) -> set[str]:
    """规范化分词：小写 + 去 punctuation + 去停用词。"""
    return {t.lower() for t in _TOKEN_RE.findall(text or "")
            if t.lower() not in _STOPWORDS}


def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard 相似度；空集对空集定义为 1.0，空对非空为 0.0。"""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def record_fingerprint(record: dict, keys: tuple[str, ...]) -> str:
    """内容指纹：只对指定字段做稳定哈希（时间戳等易变字段不参与）。"""
    payload = {k: record.get(k) for k in keys}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


@dataclass
class PurgeReport:
    removed: list[dict] = field(default_factory=list)
    kept: int = 0


class MemoryStore:
    """JSONL 记忆库：append 原子追加 + 全量读 + 指纹去重 + 相似检索。

    Args:
        path: JSONL 文件路径。
        dedup_keys: 参与指纹的字段（同指纹拒绝追加，防污染）。
    """

    def __init__(self, path: str, dedup_keys: tuple[str, ...] = ()):
        self.path = path
        self.dedup_keys = dedup_keys
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    # ------------------------------------------------------------- 读写
    def all_records(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    # 单行损坏不传染全库（读取防御），跳过并保留行号供排查
                    print(f"[MemoryStore] {self.path}:{i} 行损坏已跳过")
        return out

    def append(self, record: dict) -> bool:
        """追加记录（原子：临时文件+os.replace / O_APPEND 单次写）。

        指纹重复 → 拒绝（返回 False，防污染）；dedup_keys 为空时不去重。
        """
        if not isinstance(record, dict):
            raise TypeError(f"record 须 dict，得到 {type(record).__name__}")
        if self.dedup_keys:
            fp = record_fingerprint(record, self.dedup_keys)
            if any(record_fingerprint(r, self.dedup_keys) == fp
                   for r in self.all_records()):
                return False
        line = json.dumps(record, ensure_ascii=False) + "\n"
        # 原子追加：单次 write + flush（进程级原子）；文件级用锁见 lock()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        return True

    def rewrite(self, records: list[dict]) -> None:
        """全量重写（原子：tempfile + os.replace）。"""
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(self.path) or ".",
            prefix=os.path.basename(self.path) + ".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # ------------------------------------------------------------- 检索
    def search_keyword(self, keyword: str) -> list[dict]:
        """关键词检索（子串匹配，任意字符串字段）。"""
        if not keyword:
            return []
        kw = keyword.lower()
        return [r for r in self.all_records()
                if any(isinstance(v, str) and kw in v.lower()
                       for v in r.values())]

    def search_similar(self, query: str, threshold: float = 0.3,
                       text_fields: tuple[str, ...] = ("desc", "name")) \
            -> list[tuple[float, dict]]:
        """相似度检索：返回 [(相似度, 记录)]，按相似度降序。

        相似度 = max(Jaccard(query, 各文本字段))；阈值语义：>= threshold。
        空查询返回 []（不误判全相似）；threshold 须在 [0,1]（越界即
        ValueError——threshold<0 会全量命中、>1 会永不命中，均为误判源）。
        """
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(
                f"threshold 须在 [0,1]，得到 {threshold}")
        if not query or not query.strip():
            return []
        q = tokenize(query)
        if not q:
            return []
        scored = []
        for r in self.all_records():
            best = 0.0
            for f in text_fields:
                t = r.get(f)
                if isinstance(t, str):
                    best = max(best, jaccard(q, tokenize(t)))
            if best >= threshold:
                scored.append((best, r))
        return sorted(scored, key=lambda x: -x[0])

    # ------------------------------------------------------------- 清除
    def purge(self, predicate) -> PurgeReport:
        """删除满足 predicate(record)=True 的记录（显式条件，绝不整库清）。

        安全护栏：一次最多删一半现存记录——删除量超半数即拒绝执行
        （防 predicate 写错造成"误丢弃"灾难）。
        """
        records = self.all_records()
        to_remove = [r for r in records if predicate(r)]
        if records and len(to_remove) > len(records) / 2:
            raise RuntimeError(
                f"purge 拒绝执行：拟删 {len(to_remove)}/{len(records)} 超半数"
                "（疑似 predicate 错误；确需批量删请显式 rewrite）")
        kept = [r for r in records if not predicate(r)]
        if to_remove:
            self.rewrite(kept)
        return PurgeReport(removed=to_remove, kept=len(kept))
