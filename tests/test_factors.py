"""v0.4.0 L2 因子库 + 清洗测试。"""
import numpy as np
import pandas as pd
import pytest

from src.primitives.factors import (
    compute, compute_all, winsorize_mad, zscore, neutralize, cs_rank,
    clean_pipeline, FACTOR_REGISTRY,
)


def make_ohlc(n=60, seed=42):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = 10 + np.cumsum(rng.normal(0, 0.2, n))
    return pd.DataFrame({
        "open": close * (1 + rng.normal(0, 0.005, n)),
        "high": close * (1 + abs(rng.normal(0, 0.01, n))),
        "low": close * (1 - abs(rng.normal(0, 0.01, n))),
        "close": close,
        "volume": rng.integers(1e5, 5e5, n).astype(float),
    }, index=idx)


def make_panel(n_dates=40, n_assets=8, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-02", periods=n_dates, freq="B")
    cols = [f"S{i}" for i in range(n_assets)]
    vals = rng.normal(0, 1, (n_dates, n_assets))
    if n_dates > 10:
        vals[10, 2] = 50.0   # 注入极端值
    return pd.DataFrame(vals, index=idx, columns=cols)


# ---------------------------------------------------------------- 因子
def test_注册表包含子集():
    assert {"alpha006", "alpha012", "alpha013", "alpha044"} <= set(FACTOR_REGISTRY)


def test_compute_all_输出形状与NaN():
    df = make_ohlc(60)
    out = compute_all(df)
    assert list(out.columns) == sorted(FACTOR_REGISTRY)
    assert out.shape == (60, len(FACTOR_REGISTRY))
    # 前 10 行（最大窗口）应有 NaN（窗口不足）
    assert out.iloc[:4].isna().any().any()


def test_compute_未注册报错():
    with pytest.raises(KeyError, match="未注册"):
        compute("alpha999", make_ohlc(30))


def test_alpha006_相关性符号():
    """价量完全正相关 → alpha006 = -corr(open,vol,10) ≈ -1。"""
    n = 30
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    v = np.arange(n, dtype=float)
    df = pd.DataFrame({
        "open": v, "high": v, "low": v, "close": v, "volume": v,
    }, index=idx)
    s = compute("alpha006", df)
    assert s.iloc[-1] == pytest.approx(-1.0)


def test_alpha012_量增价跌为正():
    """sign(Δvol)>0 且 Δclose<0 → alpha012 > 0。"""
    n = 5
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    df = pd.DataFrame({
        "open": [10, 10, 10, 10, 10],
        "high": [10, 10, 10, 10, 10],
        "low": [10, 10, 10, 10, 10],
        "close": [10, 10, 10, 10, 9],
        "volume": [100, 200, 300, 400, 500],
    }, index=idx)
    s = compute("alpha012", df)
    assert s.iloc[-1] > 0


def test_因子无前视_截断不变性():
    """t 日因子值只依赖截至 t 的数据：截断后重算，前缀必须一致。"""
    df = make_ohlc(80)
    full = compute_all(df)
    for cut in (40, 60, 79):
        part = compute_all(df.iloc[:cut])
        for col in part.columns:
            a = part[col].dropna()
            b = full[col].iloc[:cut].dropna()
            pd.testing.assert_series_equal(a, b, check_names=False)


# ---------------------------------------------------------------- 清洗（按日横截面口径）
def test_winsorize_截断极端值():
    panel = make_panel()
    out = winsorize_mad(panel, n=3)
    # 注入的 50 被截断，输出 max 应显著小于 50
    assert out["S2"].max() < 50
    # 每日值落在当日横截面 MAD 边界内
    med = panel.median(axis=1)
    mad = (panel.sub(med, axis=0)).abs().median(axis=1)
    assert (out.le(med + 3 * mad + 1e-9, axis=0) |
            out.isna()).all().all()
    assert (out.ge(med - 3 * mad - 1e-9, axis=0) |
            out.isna()).all().all()


def test_zscore_每日横截面均值方差():
    panel = make_panel()
    out = zscore(panel)
    # 每行（交易日）均值 0 方差 1
    assert out.mean(axis=1).abs().max() < 1e-9
    assert (out.std(axis=1) - 1).abs().max() < 1e-9


def test_cs_rank_按行():
    panel = make_panel(10, 4)
    out = cs_rank(panel)
    # 每行最大值 rank = 1.0
    assert (out.max(axis=1) == 1.0).all()
    # rank 值域 (0,1]
    assert out.min().min() > 0


def _make_neutral_input(n_dates=60, n_assets=12, seed=3, inject_cap_exposure=True):
    """构造中性化测试数据：行业 2 类 + 市值 + 可选市值暴露。"""
    idx = pd.date_range("2024-01-02", periods=n_dates, freq="B")
    rng = np.random.default_rng(seed)
    cols = [f"S{i}" for i in range(n_assets)]
    cap_vals = rng.uniform(1e9, 5e10, n_assets)
    mktcap = pd.DataFrame({c: [cap_vals[i]] * n_dates for i, c in enumerate(cols)},
                          index=idx, columns=cols)
    industry = pd.DataFrame(
        {c: ["银行" if i % 2 == 0 else "医药" for _ in range(n_dates)]
         for i, c in enumerate(cols)}, index=idx, columns=cols)
    noise = rng.normal(0, 1, (n_dates, n_assets))
    if inject_cap_exposure:
        # 因子 = 5*log_cap + 行业效应 + 噪声（强市值暴露，中性化应剔除）
        cap_row = np.log(cap_vals)
        base = 5 * cap_row + np.array([1.0 if i % 2 == 0 else -1.0
                                       for i in range(n_assets)])
        vals = base[None, :] + noise
    else:
        vals = noise
    panel = pd.DataFrame(vals, index=idx, columns=cols)
    return panel, industry, mktcap


def test_neutralize_横截面正交():
    """每日横截面回归后，残差与 log_cap/行业不再相关（A2 回归）。"""
    panel, industry, mktcap = _make_neutral_input()
    out = neutralize(panel, industry, mktcap)
    # 每日残差与 log_cap 的相关应显著小于原始相关
    lc = np.log(mktcap)
    raw_corr = np.corrcoef(panel.iloc[0], lc.iloc[0])[0, 1]
    res_corr = np.corrcoef(out.iloc[0].dropna(), lc.iloc[0][out.iloc[0].notna()])[0, 1]
    assert abs(raw_corr) > 0.8           # 原始强暴露
    assert abs(res_corr) < 0.35          # 残差近似正交（噪声容差）
    # 每日残差均值≈0
    assert out.mean(axis=1).abs().max() < 1e-9


def test_clean_pipeline_端到端():
    panel = make_panel()
    out = clean_pipeline(panel)
    assert out.notna().any().all()
    # 每日横截面标准化后行均值≈0（未中性化时）
    assert out.mean(axis=1).abs().max() < 1e-9


def test_clean_pipeline_含中性化():
    n_dates, n_assets = 60, 6
    idx = pd.date_range("2024-01-02", periods=n_dates, freq="B")
    rng = np.random.default_rng(11)
    cols = [f"S{i}" for i in range(n_assets)]
    panel = pd.DataFrame(rng.normal(0, 1, (n_dates, n_assets)), index=idx, columns=cols)
    cap_vals = rng.uniform(1e9, 5e10, n_assets)
    mktcap = pd.DataFrame({c: [cap_vals[i]] * n_dates for i, c in enumerate(cols)},
                          index=idx, columns=cols)
    industry = pd.DataFrame({c: ["A" if i % 2 == 0 else "B" for _ in range(n_dates)]
                             for i, c in enumerate(cols)}, index=idx, columns=cols)
    out = clean_pipeline(panel, industry=industry, mktcap=mktcap)
    assert out.shape == panel.shape
    # 去极值后中性化残差有限（小样本日退化去均值告警仍输出有限值）
    assert np.isfinite(out.values[out.notna().values]).all()


# ---------------------------------------------------------------- Attacker 回归
def test_A1_清洗无前视_分布漂移不变性():
    """前半 N(0,1) 后半 N(100,1)：首日 z-score 不应被后半污染。

    若按列全时序标准化，首日均值≈50 → 首日 z 值巨大；按日横截面则 ≈N(0,1)。
    """
    n, k = 60, 8
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    rng = np.random.default_rng(5)
    vals = rng.normal(0, 1, (n, k))
    vals[n // 2:, :] = rng.normal(100, 1, (n // 2, k))
    panel = pd.DataFrame(vals, index=idx, columns=[f"S{i}" for i in range(k)])
    out = zscore(panel)
    # 前半期横截面统计量不应看到后半期的均值 100
    assert out.iloc[: n // 2].abs().max().max() < 10
    # 截断重算前缀不变（无前视的等价判据）
    part = zscore(panel.iloc[: n // 2])
    pd.testing.assert_frame_equal(out.iloc[: n // 2], part)


def test_A1_winsorize_截断不变性():
    """前缀重算 winsorize 结果一致（统计量不含未来）。"""
    panel = make_panel()
    full = winsorize_mad(panel)
    part = winsorize_mad(panel.iloc[:20])
    pd.testing.assert_frame_equal(full.iloc[:20], part)


def test_A2_中性化剔除市值暴露_按列回归无法做到():
    """因子与 log_cap 完全线性 → 横截面残差应≈噪声（原按列实现残差=原值）。"""
    n_dates, n_assets = 50, 12
    idx = pd.date_range("2024-01-02", periods=n_dates, freq="B")
    rng = np.random.default_rng(9)
    cols = [f"S{i}" for i in range(n_assets)]
    cap_vals = rng.uniform(1e9, 5e10, n_assets)
    mktcap = pd.DataFrame({c: [cap_vals[i]] * n_dates for i, c in enumerate(cols)},
                          index=idx, columns=cols)
    industry = pd.DataFrame(
        {c: ["A" if i % 3 == 0 else "B" if i % 3 == 1 else "C" for _ in range(n_dates)]
         for i, c in enumerate(cols)}, index=idx, columns=cols)
    # 因子 = 3*log_cap + 纯噪声（无行业效应）
    vals = 3 * np.log(cap_vals)[None, :] + rng.normal(0, 0.1, (n_dates, n_assets))
    panel = pd.DataFrame(vals, index=idx, columns=cols)
    out = neutralize(panel, industry, mktcap)
    # 残差标准差应 ≈ 噪声 0.1（若退化未剔除则 ≈ 3*log_cap 的 std）
    assert out.std(axis=1).mean() < 0.5


def test_A3_NaN不污染整列():
    """一条 NaN 不应让当日全部残差变 NaN。"""
    panel, industry, mktcap = _make_neutral_input(n_assets=12)
    panel.iloc[5, 0] = np.nan
    out = neutralize(panel, industry, mktcap)
    row = out.iloc[5]
    assert row.isna().sum() <= 1          # 仅缺失位为 NaN
    assert row.dropna().shape[0] >= 11    # 其余正常出残差
    # 其他日不受影响
    assert out.iloc[10].notna().all()


def test_A4_非正市值剔除不崩溃():
    """mktcap 含 0/负 → 剔除该样本并告警，其余正常。"""
    panel, industry, mktcap = _make_neutral_input(n_assets=12)
    mktcap.iloc[3, 1] = 0.0
    mktcap.iloc[3, 2] = -5.0
    with pytest.warns(UserWarning, match="非正市值"):
        out = neutralize(panel, industry, mktcap)
    row = out.iloc[3]
    assert np.isnan(row["S1"]) and np.isnan(row["S2"])
    assert row.dropna().shape[0] == 10


def test_A5_财报按披露日对齐():
    """报告期 2023-12-31、披露日 2024-03-28 → 此前交易日不得有该期值。

    序列从 2023-09-01 起（早于第一期披露日 2023-10-28）：
    - 2023-10-28 前全 NaN；
    - 2023-10-28 ~ 2024-03-27 取旧值 12.0（2023Q3 披露值）；
    - 2024-03-28 起取 15.0。
    若按报告期 end_date 对齐则 2024-01-02 起就有 15.0 → 前视。
    """
    from src.primitives.factors import align_fundamental
    dates = pd.date_range("2023-09-01", "2024-04-30", freq="B")
    fund = pd.DataFrame({
        "ann_date": ["2023-10-28", "2024-03-28"],
        "end_date": ["2023-09-30", "2023-12-31"],
        "pe": [12.0, 15.0],
    })
    out = align_fundamental(dates, fund, ["pe"])
    # 第一期披露前无值
    pre = out.loc[dates < pd.Timestamp("2023-10-28"), "pe"]
    assert pre.isna().all()
    # 两期披露日之间的值 = 旧期 12.0（不含新期 15.0 → 无前视）
    mid = out.loc[(dates >= pd.Timestamp("2023-10-28"))
                  & (dates < pd.Timestamp("2024-03-28")), "pe"]
    assert (mid == 12.0).all()
    # 新期披露日起才有新值
    after = out.loc[dates >= pd.Timestamp("2024-03-28"), "pe"]
    assert (after == 15.0).all()


def test_A5_align缺ann_date报错():
    from src.primitives.factors import align_fundamental
    dates = pd.date_range("2024-01-02", periods=5, freq="B")
    with pytest.raises(ValueError, match="ann_date"):
        align_fundamental(dates, pd.DataFrame({"pe": [1.0]}), ["pe"])
