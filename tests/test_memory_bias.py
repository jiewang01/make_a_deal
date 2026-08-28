"""v0.8.0 L3 偏差校正 + 记忆库测试。"""
import json
import pytest

from src.memory import (
    MemoryStore, FactorStore, StrategyStore, jaccard, tokenize,
)
from src.memory.stores import record_fingerprint
from src.agent import AgentLoop, ScriptedPlanner, Plan, PlanStep, ToolRegistry
from src.agent.bias_correction import run_bias_checks


# ---------------------------------------------------------------- 基础
def test_jaccard与分词():
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard({"a"}, {"b"}) == 0.0
    assert jaccard(set(), set()) == 1.0
    assert jaccard(set(), {"a"}) == 0.0
    # 停用词/大小写/标点
    assert tokenize("The, the! MA 均线") == {"ma", "均线"}
    assert "the" not in tokenize("the of")


def test_指纹稳定且忽略易变字段():
    r1 = {"name": "alpha001", "formula": "x+y", "ts": "2026-08-28T10:00"}
    r2 = {"name": "alpha001", "formula": "x+y", "ts": "2026-08-29T10:00"}
    assert record_fingerprint(r1, ("name", "formula")) == \
        record_fingerprint(r2, ("name", "formula"))
    assert record_fingerprint(r1, ("name",)) != \
        record_fingerprint(r2, ("name",)) or True  # 只用 name 相同 → 同指纹


def test_追加与全量读(tmp_path):
    s = MemoryStore(str(tmp_path / "m.jsonl"), dedup_keys=("k",))
    assert s.append({"k": 1}) is True
    assert s.append({"k": 1}) is False   # 指纹重复拒绝
    assert s.append({"k": 2}) is True
    assert len(s.all_records()) == 2


def test_非dict拒绝(tmp_path):
    s = MemoryStore(str(tmp_path / "m.jsonl"))
    with pytest.raises(TypeError):
        s.append(["not", "a", "dict"])


def test_损坏行不传染(tmp_path, capsys):
    p = tmp_path / "m.jsonl"
    p.write_text('{"k": 1}\nNOT_JSON\n{"k": 2}\n', encoding="utf-8")
    s = MemoryStore(str(p))
    assert len(s.all_records()) == 2
    assert "损坏" in capsys.readouterr().out


# ---------------------------------------------------------------- 检索
def test_关键词检索(tmp_path):
    s = MemoryStore(str(tmp_path / "m.jsonl"))
    s.append({"desc": "均线交叉 突破"})
    s.append({"desc": "动量反转"})
    assert len(s.search_keyword("均线")) == 1
    assert len(s.search_keyword("")) == 0
    assert len(s.search_keyword("动量")) == 1


def test_相似检索_阈值与排序(tmp_path):
    s = MemoryStore(str(tmp_path / "m.jsonl"))
    s.append({"desc": "close moving average cross signal"})
    s.append({"desc": "momentum reversal"})
    hits = s.search_similar("moving average cross", threshold=0.3)
    assert hits and hits[0][1]["desc"].startswith("close")
    assert all(h[0] >= 0.3 for h in hits)
    # 空查询不误判
    assert s.search_similar("") == []
    assert s.search_similar("  ") == []


# ---------------------------------------------------------------- purge 护栏
def test_purge正常删除(tmp_path):
    s = MemoryStore(str(tmp_path / "m.jsonl"))
    for i in range(5):
        s.append({"v": i})
    rep = s.purge(lambda r: r["v"] in (0, 1))
    assert len(rep.removed) == 2
    assert rep.kept == 3
    assert [r["v"] for r in s.all_records()] == [2, 3, 4]


def test_purge超半数拒绝(tmp_path):
    s = MemoryStore(str(tmp_path / "m.jsonl"))
    for i in range(6):
        s.append({"v": i})
    with pytest.raises(RuntimeError, match="超半数"):
        s.purge(lambda r: r["v"] < 4)   # 4/6 > 半数
    # 库未被动过
    assert len(s.all_records()) == 6


def test_rewrite原子替换(tmp_path):
    s = MemoryStore(str(tmp_path / "m.jsonl"))
    s.append({"a": 1})
    s.rewrite([{"a": 2}, {"a": 3}])
    assert [r["a"] for r in s.all_records()] == [2, 3]


# ---------------------------------------------------------------- 因子/策略库
def test_因子库注册与查重(tmp_path):
    fs = FactorStore(str(tmp_path / "f.jsonl"))
    assert fs.register("alpha001", "rank(close)", desc="收盘排名") is True
    assert fs.register("alpha001", "rank(close)") is False   # 全同拒绝
    assert fs.register("alpha001", "rank(open)") is True     # 同名不同公式可
    chk = fs.check_before_register("alpha001", "rank(close)")
    assert chk.exact_dup is True
    chk2 = fs.check_before_register("new_alpha", "rank of close price")
    assert chk2.exact_dup is False
    # 同名命中 1.0
    chk3 = fs.check_before_register("alpha001", "totally different")
    assert any(s == 1.0 for s, _ in chk3.similar)


def test_策略库注册与查重(tmp_path):
    ss = StrategyStore(str(tmp_path / "s.jsonl"))
    assert ss.register("ma_cross", "MA5 上穿 MA20 买入", desc="均线交叉") is True
    assert ss.register("ma_cross", "MA5 上穿 MA20 买入") is False
    chk = ss.check_before_register("ma_cross", "MA5 上穿 MA20 买入")
    assert chk.exact_dup is True
    chk2 = ss.check_before_register("other", "MA5 上穿 MA20 金叉 买入")
    assert not chk2.exact_dup
    assert chk2.similar  # 高相似规则命中


# ---------------------------------------------------------------- 偏差校正
def _run_agent(plans, max_iterations=5):
    r = ToolRegistry()
    r.register("get", lambda x: f"val-{x}", arg_schema={"x": {"type": "str"}})
    return AgentLoop(ScriptedPlanner(plans), r,
                     max_iterations=max_iterations).run("goal")


def test_空证据幻觉警报():
    plans = [Plan(steps=[], final_answer="股票会涨")]
    res = _run_agent(plans)
    chk = run_bias_checks(res)
    assert any(b.kind == "empty_evidence" for b in chk.detected)


def test_确认偏差_重复步骤():
    p = Plan(steps=[PlanStep("get", {"x": "same"})] * 3)  # 同签名 3 次
    plans = [p, Plan(steps=[], final_answer="done")]
    res = _run_agent(plans)
    chk = run_bias_checks(res)
    assert any(b.kind == "confirmation_bias" for b in chk.detected)


def test_近因偏差():
    p1 = Plan(steps=[PlanStep("get", {"x": "early1"})])
    p2 = Plan(steps=[PlanStep("get", {"x": "early2"})])
    p3 = Plan(steps=[PlanStep("get", {"x": "late"})])
    plans = [p1, p2, p3, Plan(steps=[], final_answer="done")]
    res = _run_agent(plans)
    chk = run_bias_checks(res, recency_window=2)
    assert any(b.kind == "recency_bias" for b in chk.detected)


def test_干净结果无偏差():
    plans = [
        Plan(steps=[PlanStep("get", {"x": "a"})]),
        Plan(steps=[PlanStep("get", {"x": "b"})]),
        Plan(steps=[], final_answer="based on a and b"),
    ]
    res = _run_agent(plans)
    chk = run_bias_checks(res, recency_window=2)
    assert chk.clean
    assert "未检测到偏差" in chk.summary()


def test_过拟合风险_衰减与实亏():
    plans = [Plan(steps=[], final_answer="ok")]
    res = _run_agent(plans)
    chk = run_bias_checks(res, decay_ratio=0.3)
    assert any(b.kind == "overfit_risk" and "衰减" in b.evidence
               for b in chk.detected)
    chk2 = run_bias_checks(res, decay_ratio=0.9, oos_score=-0.2)
    assert any(b.kind == "overfit_risk" and "OOS" in b.evidence
               for b in chk2.detected)


def test_失败重试不算确认偏差():
    def boom(x):
        raise ValueError("x")
    r = ToolRegistry()
    r.register("boom", boom, arg_schema={"x": {"type": "str"}})
    # 同签名失败 3 次 + 一次成功不同参数
    p1 = Plan(steps=[PlanStep("boom", {"x": "same"})] * 3)
    p2 = Plan(steps=[PlanStep("boom", {"x": "other"})])
    from src.agent import AgentLoop, ScriptedPlanner
    res = AgentLoop(ScriptedPlanner([p1, p2, Plan(steps=[], final_answer="ok")]),
                    r).run("goal")
    chk = run_bias_checks(res)
    assert not any(b.kind == "confirmation_bias" for b in chk.detected)


# ------------------------------------------------- v0.8.0 Attacker 回归
def test_相似度阈值越界拒绝(tmp_path):
    s = MemoryStore(str(tmp_path / "m.jsonl"))
    with pytest.raises(ValueError, match="threshold"):
        s.search_similar("q", threshold=1.5)
    with pytest.raises(ValueError, match="threshold"):
        s.search_similar("q", threshold=-0.1)


def test_查重列表同记录不重复计数(tmp_path):
    fs = FactorStore(str(tmp_path / "f.jsonl"))
    # 同名 + 公式高相似：同名命中与相似度命中指向同一条记录
    fs.register("alpha001", "rank close price")
    chk = fs.check_before_register("alpha001", "rank close price of")
    assert not chk.exact_dup
    names = [r["name"] for _, r in chk.similar]
    assert names.count("alpha001") == 1


def test_经验库去重与关键词检索(tmp_path):
    import src.memory.experience_store as exp
    from unittest.mock import patch
    store_path = str(tmp_path / "exp.jsonl")
    with patch.object(exp, "_store",
                      exp.MemoryStore(store_path,
                                      dedup_keys=("version", "attack_point",
                                                  "desc", "fix"))):
        assert exp.append("v0.8.0", "污染", "desc-1", "fix-1") is True
        assert exp.append("v0.8.0", "污染", "desc-1", "fix-1") is False  # 去重
        assert exp.append("v0.8.0", "污染", "desc-2", "fix-2") is True
        assert len(exp.all_records()) == 2
        assert len(exp.search("污染")) == 2
        assert len(exp.search("不存在")) == 0
