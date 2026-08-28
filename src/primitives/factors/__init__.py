"""L2 因子库 + 清洗（Alpha101 子集 + factor_cleaner）。

公开接口：
- compute / compute_all: Alpha101 子集因子（单标的时序）
- winsorize_mad / zscore / neutralize / cs_rank: 面板清洗（按日横截面）
- clean_pipeline: 去极值 → 标准化 → 中性化流水线
- align_fundamental: 财报按披露日对齐（防报告期前视）
"""
from .operators import (
    delay, delta, sign, ts_corr, ts_cov, ts_std, ts_rank,
)
from .alpha101 import (
    FACTOR_REGISTRY, register_factor, compute, compute_all,
    alpha006, alpha012, alpha013, alpha044,
)
from .cleaner import (
    winsorize_mad, zscore, neutralize, cs_rank, clean_pipeline,
    align_fundamental,
)

__all__ = [
    "delay", "delta", "sign", "ts_corr", "ts_cov", "ts_std", "ts_rank",
    "FACTOR_REGISTRY", "register_factor", "compute", "compute_all",
    "alpha006", "alpha012", "alpha013", "alpha044",
    "winsorize_mad", "zscore", "neutralize", "cs_rank", "clean_pipeline",
    "align_fundamental",
]
