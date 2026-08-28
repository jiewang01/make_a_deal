"""v0.6.0 L2 回测增强测试：Brinson 归因 + WFO + 子区间稳定性。"""
import numpy as np
import pandas as pd
import pytest

from src.primitives.backtest import (
    brinson, generate_folds, run_wfo, segment_stability,
)


# ---------------------------------------------------------------- Brinson
def test_brinson_恒等式对账():
    res = brinson(
        portfolio_weights={"A": 0.5, "B": 0.3, "C": 0.2},
        portfolio_returns={"A": 0.10, "B": 0.04, "C": 0.06},
        bench_weights={"A": 0.4, "B": 0.4, "C": 0.2},
        bench_returns={"A": 0.08, "B": 0.05, "C": 0.04},
        sectors={"A": "科技", "B": "银行", "C": "医药"},
    )
    # Σ(配置+选股+交互) = 主动收益
    assert res.reconciliation == pytest.approx(0.0, abs=1e-9)
    assert res.total_active_return == pytest.approx(
        (0.5 * 0.10 + 0.3 * 0.04 + 0.2 * 0.06) -
        (0.4 * 0.08 + 0.4 * 0.05 + 0.2 * 0.04))
    # 三分量和 = 主动收益
    assert (res.total_allocation + res.total_selection +
            res.total_interaction) == pytest.approx(res.total_active_return)


def test_brinson_纯配置场景():
    """同行业内选股相同、权重不同 → 只有配置效应，选股/交互=0。"""
    res = brinson(
        portfolio_weights={"A": 0.6, "B": 0.4},
        portfolio_returns={"A": 0.10, "B": 0.02},
        bench_weights={"A": 0.3, "B": 0.7},
        bench_returns={"A": 0.10, "B": 0.02},
        sectors={"A": "科技", "B": "银行"},
    )
    assert res.total_selection == pytest.approx(0.0, abs=1e-12)
    assert res.total_interaction == pytest.approx(0.0, abs=1e-12)
    assert res.total_allocation != 0


def test_brinson_纯选股场景():
    """权重相同、行业内选股不同 → 只有选股效应（单行业）。"""
    res = brinson(
        portfolio_weights={"A": 0.5, "B": 0.5},
        portfolio_returns={"A": 0.12, "B": 0.02},
        bench_weights={"A": 0.5, "B": 0.5},
        bench_returns={"A": 0.08, "B": 0.05},
        sectors={"A": "科技", "B": "科技"},
    )
    assert res.total_allocation == pytest.approx(0.0, abs=1e-12)
    assert res.total_interaction == pytest.approx(0.0, abs=1e-12)
    # Rp = 0.07, Rb = 0.065 → 选股 = 1×0.005
    assert res.total_selection == pytest.approx(0.005)


def test_brinson_权重和不等报错():
    with pytest.raises(ValueError, match="权重和"):
        brinson({"A": 0.6}, {"A": 0.1}, {"A": 1.0}, {"A": 0.1}, {"A": "X"})


def test_brinson_缺行业报错():
    with pytest.raises(ValueError, match="行业标签"):
        brinson({"A": 1.0}, {"A": 0.1}, {"A": 1.0}, {"A": 0.1}, {})


def test_brinson_权重收益缺口报错():
    with pytest.raises(ValueError, match="缺收益率"):
        brinson({"A": 1.0}, {}, {"A": 1.0}, {"A": 0.1}, {"A": "X"})


def test_brinson_现金显式建模():
    """组合 10% 现金（cash 行业收益 0）→ 归因无残差。"""
    res = brinson(
        portfolio_weights={"A": 0.6, "CASH": 0.4},
        portfolio_returns={"A": 0.10, "CASH": 0.0},
        bench_weights={"A": 1.0},
        bench_returns={"A": 0.08},
        sectors={"A": "科技", "CASH": "cash"},
    )
    assert res.reconciliation == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------- WFO
def test_fold切分_无重叠():
    n, tr, te, emb = 100, 40, 10, 5
    folds = list(generate_folds(n, tr, te, emb))
    assert folds, "应至少产生 1 折"
    for _, train, test in folds:
        # 训练与测试间隔 >= embargo
        assert train.stop + emb <= test.start
        assert train.stop - train.start == tr
        assert test.stop - test.start == te
    # 折之间不重叠
    tests = [range(test.start, test.stop) for _, _, test in folds]
    for i in range(len(tests) - 1):
        assert tests[i].stop <= tests[i + 1].start


def test_fold切分_样本不足无折():
    assert list(generate_folds(20, 40, 10)) == []


def test_fold切分_参数非法():
    with pytest.raises(ValueError):
        list(generate_folds(100, 0, 10))
    with pytest.raises(ValueError):
        list(generate_folds(100, 40, -1))
    with pytest.raises(ValueError):
        list(generate_folds(100, 40, 10, -1))


def test_wfo_端到端_选参只看训练窗():
    """参数 p 越大分数越高（训练/测试一致）→ 每折 best=p_max。"""
    idx = pd.date_range("2024-01-02", periods=120, freq="B")
    df = pd.DataFrame({"x": np.arange(120)}, index=idx)
    grid = [{"p": p} for p in (1, 2, 3)]

    def evaluate(params, seg):
        return float(params["p"])  # 分数与数据无关

    res = run_wfo(df, grid, evaluate, train_len=40, test_len=20, embargo=5)
    assert all(f.best_params == {"p": 3} for f in res.folds)
    assert res.oos_weighted == pytest.approx(3.0)


def test_wfo_NaN候选不参与选参():
    """p=3 在训练窗不可评估（NaN）→ 选 p=2，不选默认/随机。"""
    idx = pd.date_range("2024-01-02", periods=120, freq="B")
    df = pd.DataFrame({"x": np.arange(120)}, index=idx)
    grid = [{"p": 1}, {"p": 2}, {"p": 3}]

    def evaluate(params, seg):
        if params["p"] == 3:
            return float("nan")
        return float(params["p"])

    res = run_wfo(df, grid, evaluate, 40, 20, 5)
    assert all(f.best_params == {"p": 2} for f in res.folds)
    assert all(f.n_nan_candidates == 1 for f in res.folds)


def test_wfo_过拟合警报():
    """训练分数高、OOS 崩塌（IS=10, OOS=1 → decay=0.1 < 0.5）→ 警报。"""
    idx = pd.date_range("2024-01-02", periods=120, freq="B")
    df = pd.DataFrame({"x": np.arange(120)}, index=idx)

    def evaluate(params, seg):
        # 用段长度区分训练窗(40)/测试窗(20)——索引位置判别在折 2+ 会误判
        return 1.0 if len(seg) == 20 else 10.0

    res = run_wfo(df, [{"p": 1}], evaluate, 40, 20, 5)
    assert res.overfit_warning is True
    assert res.decay_ratio < 0.5


def test_wfo_空网格报错():
    idx = pd.date_range("2024-01-02", periods=50, freq="B")
    with pytest.raises(ValueError, match="param_grid"):
        run_wfo(pd.DataFrame({"x": [1] * 50}, index=idx), [], lambda p, s: 0.0,
                20, 10)


# ---------------------------------------------------------------- 稳定性
def _curve(values):
    idx = pd.date_range("2024-01-02", periods=len(values), freq="B")
    return pd.Series(values, index=idx, dtype=float)


def test_稳定性_平稳增长():
    eq = _curve(np.linspace(100, 150, 100))  # 各段均匀 +12%
    res = segment_stability(eq, k=5)
    assert res.stable
    assert res.positive_ratio == 1.0


def test_稳定性_单段崩塌():
    vals = list(np.linspace(100, 130, 60)) + [130 * 0.6] + \
        list(np.linspace(130 * 0.6, 130 * 0.7, 39))
    res = segment_stability(_curve(vals), k=5)
    assert not res.stable
    assert "崩塌" in res.reason or res.worst < -0.2


def test_稳定性_k大于长度报错():
    # A4: 每段至少 2 点 → len >= 2k
    with pytest.raises(ValueError, match="2×k"):
        segment_stability(_curve([100, 101]), k=2)


def test_稳定性_NaN报错():
    eq = _curve([100, 101, np.nan, 102, 103, 104])
    with pytest.raises(ValueError, match="NaN"):
        segment_stability(eq, k=2)


def test_稳定性_非正值报错():
    # k=2 时每段含偶数位置，非正值在各段中间也会被捕获（全量校验）
    eq = _curve([100, 101, -50, 60])
    with pytest.raises(ValueError, match="非正"):
        segment_stability(eq, k=2)


def test_稳定性_段收益连续拼接():
    """各段收益复合 = 截断后总收益（等长切段，段首基准=上段末尾）。"""
    vals = list(np.linspace(100, 120, 40)) + list(np.linspace(120, 90, 40)) + \
        list(np.linspace(90, 110, 41))  # 121 点，k=3 → 丢弃开头 1 点
    eq = _curve(vals)
    res = segment_stability(eq, k=3)
    # A3: 各段等长（40 点）
    compounded = 1.0
    for r in res.seg_returns:
        compounded *= 1 + r
    trimmed = eq.values[len(eq) - 40 * 3:]
    assert compounded == pytest.approx(float(trimmed[-1] / trimmed[0]))


# ---------------------------------------------------------------- Attacker 回归
def test_A1_评估异常告警且计数():
    """evaluate 抛异常 → UserWarning + n_eval_errors 计数（不静默）。"""
    idx = pd.date_range("2024-01-02", periods=120, freq="B")
    df = pd.DataFrame({"x": np.arange(120)}, index=idx)

    def evaluate(params, seg):
        if params["p"] == 9:
            raise KeyError("策略实现 bug")
        return float(params["p"])

    with pytest.warns(UserWarning, match="评估抛异常"):
        res = run_wfo(df, [{"p": 1}, {"p": 9}], evaluate, 40, 20, 5)
    assert all(f.n_eval_errors == 1 for f in res.folds)
    assert all(f.best_params == {"p": 1} for f in res.folds)


def test_A2_OOS实亏必警报():
    """IS=-0.2, OOS=-0.3：decay=1.5 不触发衰减警报，但 OOS<0 须警报。"""
    idx = pd.date_range("2024-01-02", periods=120, freq="B")
    df = pd.DataFrame({"x": np.arange(120)}, index=idx)

    def evaluate(params, seg):
        return -0.3 if len(seg) == 20 else -0.2

    res = run_wfo(df, [{"p": 1}], evaluate, 40, 20, 5)
    assert res.oos_weighted < 0
    assert res.overfit_warning is True


def test_A3_段等长切分():
    """len=101, k=5 → 丢弃开头 1 点各段恰 20 点；几何增长下段收益一致。

    首段以段首为基（无前点可链）：收益为 1.02^19-1，后续段链式基准为
    1.02^20-1，比值 ≈ 19/20 属设计语义；第 2..k 段应严格相等。
    """
    eq = _curve([100 * 1.02 ** i for i in range(101)])
    res = segment_stability(eq, k=5)
    arr = np.array(res.seg_returns)
    assert arr[1:].max() - arr[1:].min() < 1e-12   # 1.02^20 严格一致
    assert abs(arr[0] / arr[1] - 1) < 0.06         # 首段 19/20 语义差


def test_A4_单点段伪稳定被拒():
    """k=len 时每段 1 点 → 必须报错而非恒判稳定。"""
    eq = _curve(np.linspace(100, 110, 10))
    with pytest.raises(ValueError, match="2×k"):
        segment_stability(eq, k=10)
