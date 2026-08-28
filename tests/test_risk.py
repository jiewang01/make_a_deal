"""v0.5.0 L2 组合风控 + 择时测试。"""
import numpy as np
import pandas as pd
import pytest

from src.primitives.risk import (
    RiskConfig, apply_risk_gate, StopChain, atr, MATiming,
)


def make_ohlc(closes, n_needed=None):
    closes = list(map(float, closes))
    idx = pd.date_range("2024-01-02", periods=len(closes), freq="B")
    return pd.DataFrame({
        "open": [closes[0]] + closes[:-1],
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "volume": [1e6] * len(closes),
        "amount": [1e7] * len(closes),
    }, index=idx)


# ---------------------------------------------------------------- risk_gate
def test_总仓位超限等比缩放():
    w = {f"S{i}": 0.3 for i in range(4)}  # Σ=1.2 > 0.95
    res = apply_risk_gate(w)
    assert res.gross <= 0.95 + 1e-9
    assert any(v.layer == "L1_gross" for v in res.violations)
    # 等比：各票权重比例保持
    assert abs(res.weights["S0"] / res.weights["S1"] - 1.0) < 1e-9


def test_单票上限截断():
    cfg = RiskConfig(per_stock_limit=0.10)
    w = {"S0": 0.5, "S1": 0.08}
    res = apply_risk_gate(w, cfg=cfg)
    assert res.weights["S0"] == pytest.approx(0.10)
    assert res.weights["S1"] == pytest.approx(0.08)  # 未超限不动


def test_行业上限组内缩放():
    cfg = RiskConfig(per_stock_limit=0.30, per_industry_limit=0.30)
    inds = {"S0": "银行", "S1": "银行", "S2": "银行", "S3": "医药"}
    w = {"S0": 0.15, "S1": 0.15, "S2": 0.15, "S3": 0.05}  # 银行 0.45 > 0.3
    res = apply_risk_gate(w, industries=inds, cfg=cfg)
    bank = res.weights["S0"] + res.weights["S1"] + res.weights["S2"]
    assert bank <= 0.30 + 1e-9
    assert any(v.layer == "L3_industry" for v in res.violations)
    assert res.weights["S3"] == pytest.approx(0.05)  # 其他行业不受影响
    # 组内等比：三票比例保持
    r = [res.weights[f"S{i}"] / bank for i in range(3)]
    assert max(r) - min(r) < 1e-9


def test_流动性参与率截断():
    # 组合 1e6，参与率 5% → S0 可交易 5e4/2e5=0.25；S1 充裕 5.0
    cfg = RiskConfig(per_stock_limit=0.30)
    w = {"S0": 0.4, "S1": 0.2}
    amounts = {"S0": 2e5, "S1": 1e8}
    res = apply_risk_gate(w, amounts=amounts, portfolio_value=1e6, cfg=cfg)
    assert res.weights["S0"] <= 0.25 + 1e-9
    assert res.weights["S1"] == pytest.approx(0.2)  # 流动性充裕不裁
    assert any(v.layer == "L4_liquidity" for v in res.violations)


def test_零成交额极端行情置零():
    w = {"S0": 0.2, "S1": 0.2}
    amounts = {"S0": 0.0, "S1": 1e8}
    res = apply_risk_gate(w, amounts=amounts, portfolio_value=1e6)
    assert res.weights["S0"] == 0.0
    assert res.weights["S1"] > 0


def test_NaN与负权重清洗():
    cfg = RiskConfig(per_stock_limit=0.30)
    w = {"S0": float("nan"), "S1": -0.5, "S2": 0.3}
    res = apply_risk_gate(w, cfg=cfg)
    assert res.weights["S0"] == 0.0
    assert res.weights["S1"] == 0.0
    assert res.weights["S2"] == pytest.approx(0.3)


def test_风控只缩不放():
    """任何配置下，输出各票权重 ≤ 输入（不放大）。"""
    w = {"S0": 0.05, "S1": 0.05}
    res = apply_risk_gate(w, industries={"S0": "A", "S1": "B"},
                          amounts={"S0": 1e9, "S1": 1e9},
                          portfolio_value=1e6)
    for k, v in w.items():
        assert res.weights[k] <= v + 1e-12


def test_终检绝不穿透():
    """大权重 + 多层叠加后 Σ 恒 ≤ gross_limit。"""
    w = {f"S{i}": 0.09 for i in range(20)}  # Σ=1.8
    inds = {f"S{i}": f"IND{i // 5}" for i in range(20)}  # 每行业 5 票=0.45>0.3
    res = apply_risk_gate(w, industries=inds,
                          amounts={f"S{i}": 1e9 for i in range(20)},
                          portfolio_value=1e8)
    assert res.gross <= 0.95 + 1e-6


# ---------------------------------------------------------------- stops
def test_ATR计算_与手算一致():
    df = make_ohlc([10, 11, 12, 11, 10, 9, 10, 11, 12, 13, 12, 11, 10, 11, 12, 13])
    a = atr(df, 14)
    assert a.notna().iloc[-1]
    # 手算最后 14 根的 TR 均值
    h, l, pc = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    assert a.iloc[-1] == pytest.approx(tr.iloc[-14:].mean())


def test_百分比止损触发():
    df = make_ohlc([10] * 5 + [9.0])  # 入口 10，跌 10% 价位 9.2，low≈8.91<9.2
    chain = StopChain(10.0, pct=0.08, atr_n=None, trail=None)
    bar = df.iloc[-1]
    ev = chain.check(bar)
    assert ev is not None and ev.kind == "pct"
    assert ev.stop_price == pytest.approx(10 * 0.92)


def test_跳空缺口_成交价取开盘():
    """止损价 9.2，次日开盘 8.5（跳空低开）→ 退出价应取 8.5 而非 9.2。"""
    df = make_ohlc([10] * 5 + [9.0])
    chain = StopChain(10.0, pct=0.08, atr_n=None, trail=None)
    ev = chain.check(df.iloc[-1], next_open=8.5)
    assert ev.exit_price == pytest.approx(8.5)
    # 未跳空（开盘 9.5 > 止损价）→ 取止损价
    ev2 = chain.check(df.iloc[-1], next_open=9.5)
    assert ev2.exit_price == pytest.approx(9.2)


def test_ATR止损_入口ATR为零禁用并告警():
    with pytest.warns(UserWarning, match="ATR"):
        chain = StopChain(10.0, pct=None, atr_n=14, atr_mult=2.0,
                          trail=None, entry_atr=0.0)
    assert chain.atr_stop is None


def test_追踪止损():
    """峰值 12，回撤 10% 价位 10.8；随后 low<10.8 触发。"""
    closes = [10, 10, 11, 12, 12, 11.5, 11.0]
    df = make_ohlc(closes)
    chain = StopChain(10.0, pct=None, atr_n=None, trail=0.10)
    for i in range(len(df)):
        chain.update(df.iloc[i])
    # 峰值 close=12 → trail 价 10.8；最后一根 close=11 → low=10.89 < 10.8? 不
    # 构造击穿：close=11.5 → low=11.385 > 10.8 未触发；再一根 close=11.0 low=10.89 未触发
    # 显式击穿
    df2 = make_ohlc([10, 10, 11, 12, 11.9, 11.5])
    chain2 = StopChain(10.0, pct=None, atr_n=None, trail=0.10)
    for i in range(len(df2)):
        chain2.update(df2.iloc[i])
    assert chain2.peak_close == pytest.approx(12.0)
    bar = pd.Series({"low": 10.5, "close": 11.0}, name=df2.index[-1])
    ev = chain2.check(bar)
    assert ev is not None and ev.kind == "trailing"
    assert ev.stop_price == pytest.approx(12 * 0.9)


def test_止损不触发时无事件():
    df = make_ohlc([10, 10.5, 11, 11.5, 12])
    chain = StopChain(10.0, pct=0.08, atr_n=None, trail=0.10)
    ev = None
    for i in range(len(df)):
        chain.update(df.iloc[i])
        ev = chain.check(df.iloc[i]) or ev
    assert ev is None


def test_三种止损取最深():
    """同时命中 pct(9.2) 与 trailing(9.0) → 取更深的 9.0（更早触发者为准）。"""
    bar = pd.Series({"low": 8.5, "close": 9.5}, name=pd.Timestamp("2024-01-10"))
    chain = StopChain(10.0, pct=0.08, atr_n=None, trail=0.10)
    chain.peak_close = 10.0  # trail 价 9.0
    ev = chain.check(bar)
    assert ev is not None
    assert ev.stop_price == pytest.approx(9.0)
    assert ev.kind == "trailing"


# ---------------------------------------------------------------- timing
def test_均线择时_趋势上下():
    n = 30
    up = pd.Series(np.linspace(10, 20, n))
    dn = pd.Series(np.linspace(20, 10, n))
    t = MATiming(n=20)
    assert t.is_on(up) is True
    assert t.is_on(dn) is False


def test_择时窗口不足保守False():
    t = MATiming(n=200)
    assert t.is_on(pd.Series([10, 11, 12])) is False


def test_exit_on_off():
    t_off = MATiming(n=20, exit_on_off=False)
    t_exit = MATiming(n=20, exit_on_off=True)
    dn = pd.Series(np.linspace(20, 10, 30))
    assert t_off.should_exit(dn) is False   # 仅停开新仓
    assert t_exit.should_exit(dn) is True   # 强制清仓


def test_择时周期非法():
    with pytest.raises(ValueError, match="周期"):
        MATiming(n=1)


# ---------------------------------------------------------------- Attacker 回归
def test_A1_入口日豁免止损():
    """入口当日 low 击穿 pct 价位 → 不触发（基准当日才成立）。"""
    # 入口价 10，pct=8% → 价位 9.2；当日 low 9.0 击穿但为入口日
    bar = pd.Series({"low": 9.0, "close": 9.5}, name=pd.Timestamp("2024-01-05"))
    chain = StopChain(10.0, pct=0.08, atr_n=None, trail=None)
    assert chain.check(bar, is_entry_day=True) is None
    # 非入口日同 bar → 触发
    ev = chain.check(bar, is_entry_day=False)
    assert ev is not None and ev.kind == "pct"


def test_A2_停牌禁增不禁持():
    """无成交额：存量持仓保留 + 告警记录；新仓置零。"""
    w = {"S0": 0.2, "S1": 0.2}  # S0 持有 0.2，S1 新仓
    amounts = {"S0": 0.0, "S1": 1e8}
    holdings = {"S0": 0.2}
    res = apply_risk_gate(w, amounts=amounts, portfolio_value=1e6,
                          holdings=holdings)
    assert res.weights["S0"] == pytest.approx(0.2)   # 存量保留
    assert any("禁增不禁持" in v.detail for v in res.violations)
    assert res.weights["S1"] > 0                      # 正常票不受影响
    # 想加仓（目标 0.3 > 持仓 0.2）→ 置零（买不进；放宽单票上限隔离 L4 逻辑）
    cfg_wide = RiskConfig(per_stock_limit=0.5)
    res2 = apply_risk_gate({"S0": 0.3}, amounts=amounts,
                           portfolio_value=1e6, holdings=holdings,
                           cfg=cfg_wide)
    assert res2.weights["S0"] == 0.0


def test_A3_择时空序列不崩溃():
    t = MATiming(n=5)
    assert t.is_on(pd.Series(dtype=float)) is False
    assert t.should_exit(pd.Series(dtype=float)) is False


def test_A4_止损参数校验():
    with pytest.raises(ValueError, match="pct"):
        StopChain(10.0, pct=-0.1, atr_n=None, trail=None)
    with pytest.raises(ValueError, match="pct"):
        StopChain(10.0, pct=1.5, atr_n=None, trail=None)
    with pytest.raises(ValueError, match="trail"):
        StopChain(10.0, pct=None, atr_n=None, trail=0.0)
    with pytest.raises(ValueError, match="atr_n"):
        StopChain(10.0, pct=None, atr_n=0, atr_mult=2.0, entry_atr=1.0)
    with pytest.raises(ValueError, match="atr_mult"):
        StopChain(10.0, pct=None, atr_n=14, atr_mult=-1.0, entry_atr=1.0)
