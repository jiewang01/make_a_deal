"""数据加载器：多源 + Parquet 缓存 + 增量更新。

防攻击面设计：
- 复权一致性：缓存路径含 adj（_cache_path），不同复权物理隔离，绝不混用。
- 前视偏差：fetch 接口可选 as_of_date，回测端不得晚于该日取数；
  实现会裁掉晚于 as_of_date 的数据。
- 增量更新：读已有 Parquet 的最后日期，仅拉新数据，避免全量重复请求。
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
import pandas as pd

from .base import get_source

# 缓存目录（相对项目根）。可被 config 覆盖。
DATA_DIR = Path("data/parquet")


def _cache_path(code: str, adj: str) -> Path:
    """缓存路径含 adj，防不同复权混用（Attacker 攻击面：复权错误）。"""
    return DATA_DIR / f"{code}_{adj}.parquet"


def _norm_date(s: str) -> str:
    """统一为 YYYYMMDD。"""
    return s.replace("-", "")


def load_daily(code: str, start: str, end: str, *,
              source: str = "akshare",
              adj: str = "qfq",
              as_of_date: Optional[str] = None,
              force_refresh: bool = False) -> pd.DataFrame:
    """加载日线行情（带 Parquet 缓存 + 增量更新）。

    Args:
        code: 证券代码 "600519"。
        start/end: "YYYY-MM-DD"。
        source: 数据源名（akshare/tushare/...）。
        adj: 复权 qfq/hfq/none。缓存按 adj 隔离。
        as_of_date: 前视偏差防护，裁掉晚于该日数据。回测必传。
        force_refresh: True 忽略缓存全量重拉。
    Returns:
        DataFrame index=date, columns=[open,high,low,close,volume,amount]
    """
    src = get_source(source)
    path = _cache_path(code, adj)
    cached = None
    if path.exists() and not force_refresh:
        cached = pd.read_parquet(path)

    # 修复 A4 缓存污染：若传 as_of_date，先裁掉缓存中晚于该日的数据，
    # 避免含未来数据的旧缓存被写回污染缓存层（返回层有 mask 但缓存层须清理）
    if cached is not None and as_of_date:
        cached = cached[cached.index <= pd.Timestamp(as_of_date)]

    # 修复 A1 复权基准漂移：qfq/hfq 以最新交易日为基准，增量拼接不同基准的
    # 复权数据会致价格跳变 → 强制全量重拉覆盖。建议改 adj='none'+复权因子(后续版本)
    if adj != "none" and cached is not None and not force_refresh:
        import warnings
        warnings.warn(
            f"adj={adj} 复权基准随最新交易日漂移，已禁用增量更新全量重拉；"
            "建议 adj='none' + 复权因子(后续版本)提升性能",
            stacklevel=2,
        )
        force_refresh = True
        cached = None

    # 增量起点：缓存末尾的下一天
    fetch_start = _norm_date(start)
    if cached is not None and len(cached):
        last = cached.index.max()
        fetch_start = (last + pd.Timedelta(days=1)).strftime("%Y%m%d")

    end_norm = _norm_date(end)
    need_fetch = (cached is None) or (fetch_start <= end_norm)

    if need_fetch:
        new = src.daily(code, fetch_start, end_norm, adj=adj)
        # A5 健壮性：确保 index 为 datetime（source 可能返回 RangeIndex 等非 datetime）
        new.index = pd.to_datetime(new.index)
        # 前视偏差防护：裁掉晚于 as_of_date 的数据
        if as_of_date:
            new = new[new.index <= pd.Timestamp(as_of_date)]
        if cached is not None and len(new):
            df = pd.concat([cached, new])
            df = df[~df.index.duplicated(keep="last")].sort_index()
        elif cached is None:
            df = new
        else:  # cached 有 new 空
            df = cached
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path)
    else:
        df = cached if cached is not None else pd.DataFrame()

    # 按请求区间裁剪
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    if as_of_date:
        mask = mask & (df.index <= pd.Timestamp(as_of_date))
    return df[mask].copy()
