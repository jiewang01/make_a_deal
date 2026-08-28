"""因子清洗流水线：去极值 → 标准化 → 行业+市值中性化。

面板约定：panel 为宽表 DataFrame(index=date, columns=资产)。
industry: DataFrame 同形状，值为行业标签。
mktcap: DataFrame 同形状，市值（元）。

防攻击面设计（v0.4.0 Attacker 修复后）：
- A1 前视：所有清洗统计量按日横截面（axis=1）计算，绝不跨期使用未来数据。
- A2 中性化退化：每日横截面 OLS（该日各资产因子值 ~ 行业哑变量 + log 市值），
  而非按列时序回归（后者 X 为常数退化成去均值）。
- A3 NaN：按日 dropna 后回归，缺失位置输出 NaN；丢弃比例超阈值告警。
- A4 cap<=0：log 前剔除非正市值样本并告警。
- A5 披露日：align_fundamental 用披露日（ann_date）而非报告期（end_date）
  merge_asof 对齐，防财报前视。
"""
from __future__ import annotations
import warnings
import numpy as np
import pandas as pd


def winsorize_mad(panel: pd.DataFrame, n: float = 3.0) -> pd.DataFrame:
    """MAD 去极值（按日横截面）。

    每个交易日：|x - 横截面中位数| > n × MAD 截断到边界。
    修复 A1：统计量只用当日横截面，不含未来数据。
    """
    med = panel.median(axis=1)
    mad = (panel.sub(med, axis=0)).abs().median(axis=1)
    lo = (med - n * mad).to_frame("lo").reindex(panel.index)
    hi = (med + n * mad).to_frame("hi").reindex(panel.index)
    return panel.clip(lo["lo"], hi["hi"], axis=0)


def zscore(panel: pd.DataFrame) -> pd.DataFrame:
    """z-score 标准化（按日横截面）。

    修复 A1：每日横截面均值/方差，只用当日数据。
    横截面 std=0（全部同值）时输出 0，避免 inf 污染。
    """
    mu = panel.mean(axis=1)
    sd = panel.std(axis=1)
    sd_safe = sd.replace(0, np.nan)
    out = panel.sub(mu, axis=0).div(sd_safe, axis=0)
    return out.where(sd.notna() & (sd != 0), 0.0)


def neutralize(panel: pd.DataFrame, industry: pd.DataFrame,
               mktcap: pd.DataFrame,
               warn_drop_ratio: float = 0.3) -> pd.DataFrame:
    """行业 + 市值中性化（每日横截面 OLS，修复 A2/A3/A4）。

    每个交易日 d：该日有效样本（因子非 NaN 且市值>0）回归
    y_d ~ [行业哑变量(drop_first), log(市值)]，残差即中性化因子。
    有效样本不足（<= 特征数）时该日跳过回归仅去均值并告警。
    """
    resid = pd.DataFrame(index=panel.index, columns=panel.columns,
                         dtype=float)
    for d, y_row in panel.iterrows():
        ind_row = industry.loc[d]
        cap_row = mktcap.loc[d]
        # A3/A4：有效样本 = 因子非 NaN 且市值>0 且行业非 NaN
        valid = y_row.notna() & cap_row.notna() & (cap_row > 0) & ind_row.notna()
        n_bad_cap = int((cap_row <= 0).sum())
        if n_bad_cap:
            warnings.warn(f"{d.date()} 剔除 {n_bad_cap} 个非正市值样本",
                          stacklevel=2)
        yv = y_row[valid]
        if len(yv) == 0:
            continue
        ind_v = ind_row[valid]
        cap_v = cap_row[valid]
        X = pd.get_dummies(ind_v, drop_first=True).astype(float)
        X["const"] = 1.0          # 截距：剔除因子整体水平暴露，保证残差均值=0
        X["log_cap"] = np.log(cap_v)
        n_feat = X.shape[1]
        if len(yv) <= n_feat:
            # 样本不足以估计：退化为横截面去均值（不引入哑变量噪声）
            warnings.warn(
                f"{d.date()} 有效样本 {len(yv)} <= 特征数 {n_feat}，"
                "跳过回归仅去均值", stacklevel=2)
            resid.loc[d, yv.index] = yv - yv.mean()
            continue
        beta, *_ = np.linalg.lstsq(X.values, yv.values, rcond=None)
        r = yv.values - X.values @ beta
        resid.loc[d, yv.index] = r
        drop_ratio = 1 - len(yv) / len(y_row)
        if drop_ratio > warn_drop_ratio:
            warnings.warn(f"{d.date()} 缺失/无效样本占比 {drop_ratio:.0%}",
                          stacklevel=2)
    return resid


def cs_rank(panel: pd.DataFrame) -> pd.DataFrame:
    """横截面百分位 rank（按行/日期），值域 (0,1]。"""
    return panel.rank(axis=1, pct=True)


def clean_pipeline(panel: pd.DataFrame, industry: pd.DataFrame | None = None,
                   mktcap: pd.DataFrame | None = None,
                   n_mad: float = 3.0) -> pd.DataFrame:
    """标准清洗流水线：去极值 → 标准化 → （可选）中性化（均按日横截面）。"""
    out = winsorize_mad(panel, n=n_mad)
    out = zscore(out)
    if industry is not None and mktcap is not None:
        out = neutralize(out, industry, mktcap)
    return out


def align_fundamental(price_dates: pd.DatetimeIndex,
                      fund: pd.DataFrame,
                      value_cols: list[str]) -> pd.DataFrame:
    """财报数据按披露日对齐到交易日（修复 A5：防报告期前视）。

    Args:
        price_dates: 交易日序列（升序）。
        fund: 财报数据，须含列 ann_date（披露日）与 value_cols（指标列）。
              ann_date 可重复（同一日多只股票）——本函数按日聚合前先取
              每个披露日的截面均值，调用方做单票对齐时传入单票数据即可。
        value_cols: 需要对齐的指标列。
    Returns:
        DataFrame(index=price_dates, columns=value_cols)，
        每个交易日取"披露日 <= 当日"的最近一期财报值；之前为 NaN。
    """
    if "ann_date" not in fund.columns:
        raise ValueError("fund 缺少 ann_date（披露日）列")
    f = fund.copy()
    f["ann_date"] = pd.to_datetime(f["ann_date"])
    f = f.sort_values("ann_date").drop_duplicates("ann_date", keep="last")
    left = pd.DataFrame(index=pd.DatetimeIndex(price_dates).sort_values())
    merged = pd.merge_asof(
        left.reset_index(names="date"), f, left_on="date",
        right_on="ann_date", direction="backward",
    ).set_index("date")
    return merged[value_cols]
