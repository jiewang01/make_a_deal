"""v0.3.0 L2 回测引擎测试：覆盖前视/T+1/涨跌停/费用/整手/端到端。"""
import pandas as pd
import pytest

from src.primitives.backtest import (
    AshareFeeConfig, BacktestEngine, MACross, Position, Signal,
    Strategy, ScriptStrategy, is_limit_up, is_limit_down, round_to_lot,
    limit_prices,
)


def make_df(closes, opens=None):
    """合成日线行情。open 默认取前收（无跳空），可显式构造触板场景。"""
    closes = list(map(float, closes))
    if opens is None:
        opens = [closes[0]] + closes[:-1]
    opens = list(map(float, opens))
    idx = pd.date_range("2024-01-02", periods=len(closes), freq="B")
    return pd.DataFrame({
        "open": opens,
        "high": [max(o, c) * 1.01 for o, c in zip(opens, closes)],
        "low": [min(o, c) * 0.99 for o, c in zip(opens, closes)],
        "close": closes,
        "volume": [1e6] * len(closes),
        "amount": [1e7] * len(closes),
    }, index=idx)


# ---------------------------------------------------------------- 费用模型
def test_费用模型_项项到位():
    fee = AshareFeeConfig()
    assert fee.commission(1000) == 5.0            # 最低佣金 5 元
    assert fee.commission(1_000_000) == pytest.approx(250.0)   # 万 2.5
    assert fee.stamp_tax(1000, is_sell=True) == pytest.approx(0.5)   # 千 0.5
    assert fee.stamp_tax(1000, is_sell=False) == 0.0                  # 仅卖出
    assert fee.transfer_fee(1000) == pytest.approx(0.01)   # 万 0.1 双向
    assert fee.slippage_price(10.0, is_buy=True) > 10.0    # 买入上浮
    assert fee.slippage_price(10.0, is_buy=False) < 10.0   # 卖出下浮


# ---------------------------------------------------------------- 涨跌停板
def test_涨跌停板_判定():
    up, down = limit_prices(10.0)
    assert up == 11.0 and down == 9.0
    assert is_limit_up(11.0, 10.0)       # 开盘=涨停 → 买不进
    assert not is_limit_up(10.99, 10.0)
    assert is_limit_down(9.0, 10.0)      # 开盘=跌停 → 卖不出
    assert not is_limit_down(9.01, 10.0)


def test_涨停开盘_买入被拒():
    """信号日 close=10，次日 open=11 触涨停 → 买单拒，当日无买入成交。"""
    df = make_df([10, 10, 10], opens=[10, 11, 10])
    eng = BacktestEngine(df, ScriptStrategy({0: Signal("buy", 1.0)}),
                         cash=100_000)
    res = eng.run()
    r = [x for x in res.rejects if x.reason == "limit_up"]
    assert r and r[0].date == df.index[1]
    assert not any(f.action == "buy" and f.date == df.index[1] for f in res.fills)


def test_跌停开盘_卖出被拒():
    """持有后信号日 close=10，次日 open=9 触跌停 → 卖单拒，持仓滞留。"""
    df = make_df([10, 10, 10], opens=[10, 10, 9])
    plan = {0: Signal("buy", 1.0), 1: Signal("sell", 1.0)}
    eng = BacktestEngine(df, ScriptStrategy(plan), cash=100_000)
    res = eng.run()
    # t=0 信号 → t=1 买入成交（open=10 未触板）
    assert any(f.action == "buy" and f.date == df.index[1] for f in res.fills)
    # t=1 信号 → t=2 卖出（open=9 跌停，prev_close=10）拒
    r = [x for x in res.rejects if x.reason == "limit_down"]
    assert r and r[0].date == df.index[2]
    assert not any(f.action == "sell" for f in res.fills)
    assert eng.position.total_shares > 0  # 持仓滞留


# ---------------------------------------------------------------- T+1
def test_T加1_买入当日不可卖():
    pos = Position()
    d = pd.Timestamp("2024-01-02")
    pos.add(200, d, 10.0)
    assert pos.sellable_shares(d) == 0                    # 当日不可卖
    assert pos.sellable_shares(d + pd.Timedelta(days=1)) == 200  # 次日可卖


def test_T加1_引擎内_当日买入次日信号才能卖():
    """t=0 信号买入(t=1 成交，lot.buy_date=idx1)；t=1 信号卖出 → t=2 成交合法。"""
    df = make_df([10, 10, 10])
    plan = {0: Signal("buy", 1.0), 1: Signal("sell", 1.0)}
    eng = BacktestEngine(df, ScriptStrategy(plan), cash=100_000)
    res = eng.run()
    assert [f.action for f in res.fills] == ["buy", "sell"]
    sell = res.fills[1]
    assert sell.date == df.index[2]  # 卖出在买入 lot 的次日
    assert sell.signal_date == df.index[1]


# ---------------------------------------------------------------- 前视偏差
def test_前视_策略只见历史_成交在信号次日():
    """on_bar 收到的 df 最后一行必须是信号日本身；成交日=信号日下一交易日。"""
    df = make_df([10 - i * 0.1 for i in range(30)])  # 单边跌
    seen = []

    class Spy(Strategy):
        def on_bar(self, d):
            seen.append(d.index[-1])
            return None

    plan_df = df
    eng = BacktestEngine(plan_df, Spy(), cash=100_000)
    eng.run()
    # 策略可见的最后日期 <= 倒数第二根 bar（最后一日不生成信号，更不能见到未来）
    assert all(d <= df.index[-2] for d in seen)
    # 每根 bar（除最后一根）都被喂给策略且截至当日
    assert len(seen) == len(df) - 1
    assert seen[-1] == df.index[-2]


def test_前视_均线交叉成交均在信号次日():
    closes = ([20 - i * 0.2 for i in range(25)] +
              [15 + i * 0.3 for i in range(25)] +
              [22.5 - i * 0.3 for i in range(25)])
    df = make_df(closes)
    eng = BacktestEngine(df, MACross(5, 20), cash=1_000_000)
    res = eng.run()
    assert res.fills, "应产生成交"
    for f in res.fills:
        i = df.index.get_loc(f.signal_date)
        assert f.date == df.index[i + 1]  # 成交严格在信号次日
        assert f.date > f.signal_date


# ---------------------------------------------------------------- 整手/资金
def test_整手_不足100股不成交():
    """cash=1000, open=10 → 含滑点价约 10.01 → 仅够 99 股 → 拒。"""
    df = make_df([10, 10, 10])
    eng = BacktestEngine(df, ScriptStrategy({0: Signal("buy", 1.0)}),
                         cash=1000)
    res = eng.run()
    assert res.fills == []
    assert res.rejects[0].reason == "insufficient_cash"


def test_整手_买入数量为100整数倍():
    df = make_df([10, 10, 10])
    eng = BacktestEngine(df, ScriptStrategy({0: Signal("buy", 1.0)}),
                         cash=99_999)
    res = eng.run()
    buy = res.fills[0]
    assert buy.shares % 100 == 0
    assert buy.shares > 0


def test_资金不透支_买入后现金非负():
    df = make_df([10, 10, 10])
    eng = BacktestEngine(df, ScriptStrategy({0: Signal("buy", 1.0)}),
                         cash=5000)
    res = eng.run()
    assert eng.cash >= 0
    buy = res.fills[0]
    # 现金 = 5000 - 成交额 - 佣金 - 过户费
    assert eng.cash == pytest.approx(
        5000 - buy.shares * buy.price - buy.commission - buy.transfer_fee)


# ---------------------------------------------------------------- 权益与费用入账
def test_权益计算_现金加持仓市值():
    df = make_df([10, 12, 12, 12])
    eng = BacktestEngine(df, ScriptStrategy({0: Signal("buy", 1.0)}),
                         cash=10_000)
    res = eng.run()
    buy = res.fills[0]
    # 买入成交后各日权益 = 现金 + 股数×收盘
    for i in (1, 2, 3):
        expect = eng.cash + buy.shares * float(df["close"].iloc[i])
        assert res.equity_curve.iloc[i] == pytest.approx(expect)


def test_费用入账_卖出含印花税买入不含():
    df = make_df([10, 10, 10])
    plan = {0: Signal("buy", 1.0), 1: Signal("sell", 1.0)}
    eng = BacktestEngine(df, ScriptStrategy(plan), cash=2000)
    res = eng.run()
    buy, sell = res.fills
    assert buy.stamp_tax == 0.0          # 买入无印花税
    assert sell.stamp_tax == pytest.approx(sell.shares * sell.price * 0.0005)
    assert buy.commission >= 5.0 and sell.commission >= 5.0  # 最低佣金
    assert buy.transfer_fee > 0 and sell.transfer_fee > 0    # 过户费双向
    # 滑点方向
    assert buy.price > 10.0 and sell.price < 10.0


# ---------------------------------------------------------------- 端到端
def test_均线交叉端到端_先买后卖():
    closes = ([20 - i * 0.2 for i in range(25)] +
              [15 + i * 0.3 for i in range(25)] +
              [22.5 - i * 0.3 for i in range(25)])
    df = make_df(closes)
    eng = BacktestEngine(df, MACross(5, 20), cash=1_000_000, code="600519")
    res = eng.run()
    actions = [f.action for f in res.fills]
    assert "buy" in actions and "sell" in actions
    assert actions.index("buy") < actions.index("sell")
    m = res.metrics
    assert m["n_fills"] == len(res.fills)
    assert m["final_equity"] == pytest.approx(float(res.equity_curve.iloc[-1]))
    assert m["max_drawdown"] <= 0
    assert m["n_round_trips"] >= 1
    assert res.equity_curve.iloc[0] == pytest.approx(1_000_000)  # 期初无持仓


def test_行情缺列报错():
    with pytest.raises(ValueError, match="缺列"):
        BacktestEngine(pd.DataFrame({"close": [1, 2, 3]}), MACross())


def test_行情过短报错():
    with pytest.raises(ValueError, match="不足"):
        BacktestEngine(make_df([10, 10]), MACross())


# ---------------------------------------------------------------- Attacker 回归
def test_A1_滑点不穿透涨停板():
    """open=10.99 未触板(涨停 11.0)，滑点后 11.00089>11.0 → clamp 至 11.0。"""
    df = make_df([10, 10, 10], opens=[10, 10.99, 10])
    eng = BacktestEngine(df, ScriptStrategy({0: Signal("buy", 1.0)}),
                         cash=1_000_000)
    res = eng.run()
    buy = [f for f in res.fills if f.date == df.index[1]]
    assert buy, "未触板应成交"
    assert buy[0].price <= 11.0  # 成交价不穿透涨停


def test_A1_滑点不穿透跌停板():
    """持有后 open=9.01 未触板(跌停 9.0)，滑点价 clamp 至 >= 9.0。"""
    df = make_df([10, 10, 10], opens=[10, 10, 9.01])
    plan = {0: Signal("buy", 1.0), 1: Signal("sell", 1.0)}
    eng = BacktestEngine(df, ScriptStrategy(plan), cash=1_000_000)
    res = eng.run()
    sells = [f for f in res.fills if f.action == "sell" and f.date == df.index[2]]
    assert sells, "未触板应成交"
    assert sells[0].price >= 9.0  # 成交价不穿透跌停


def test_A2_已实现盈亏含双边费用():
    """round-trip 盈亏 = 卖出净得 - 买入含费成本，双边费用都要扣。

    用零滑点隔离纯费用口径：价格不变时唯一损耗即双边费用。
    """
    df = make_df([10, 10, 10])
    plan = {0: Signal("buy", 1.0), 1: Signal("sell", 1.0)}
    eng = BacktestEngine(df, ScriptStrategy(plan), cash=2000,
                         fee=AshareFeeConfig(slippage_rate=0.0))
    res = eng.run()
    buy, sell = res.fills
    total_fee = (buy.commission + buy.transfer_fee +
                 sell.commission + sell.stamp_tax + sell.transfer_fee)
    # 期末权益 = 2000 - 双边全部费用（零滑点且价格不变，无价差损益）
    assert res.metrics["final_equity"] == pytest.approx(2000 - total_fee)
    # 已实现盈亏为负（纯费用），若漏算买入费则亏损被低估
    assert eng.realized_pnls[0] == pytest.approx(-total_fee)


def test_A3_期末敞口可见():
    """未平仓时 metrics 暴露持仓股数与市值。"""
    df = make_df([10, 10, 12])
    eng = BacktestEngine(df, ScriptStrategy({0: Signal("buy", 1.0)}),
                         cash=10_000)
    res = eng.run()
    m = res.metrics
    assert m["open_position_shares"] > 0
    assert m["open_position_value"] == pytest.approx(
        m["open_position_shares"] * 12.0)


def test_A4_短样本年化夏普为None():
    """样本 < 63 交易日，年化/夏普置 None 防虚假膨胀。"""
    df = make_df([10, 11, 12, 11, 10, 9])
    eng = BacktestEngine(df, ScriptStrategy({}), cash=10_000)
    res = eng.run()
    assert res.metrics["annual_return"] is None
    assert res.metrics["sharpe"] is None
    assert res.metrics["total_return"] is not None  # 总收益仍有效
