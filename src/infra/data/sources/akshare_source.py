"""AKShare 数据源（免费，无需 token）。

注意（Attacker 关注点）：
- akshare 的 index_components 默认返回【最新】成分股，存在幸存者偏差。
  历史回测务必改用 tushare 时点成分接口，或在本源标注 as_of_date 不支持。
- 复权：akshare stock_zh_a_hist 的 adjust 参数 qfq/hfq/""（空串=不复权）。
"""
from __future__ import annotations
from typing import Optional
import pandas as pd

from ..base import DataSource, register_source


@register_source("akshare")
class AKShareSource(DataSource):
    """AKShare 数据源。"""

    # akshare adjust 参数映射
    _ADJ_MAP = {"qfq": "qfq", "hfq": "hfq", "none": ""}

    def daily(self, code: str, start: str, end: str, adj: str = "qfq") -> pd.DataFrame:
        import akshare as ak
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=_norm(start), end_date=_norm(end),
            adjust=self._ADJ_MAP.get(adj, ""),
        )
        # 统一列名
        rename = {"日期": "date", "开盘": "open", "收盘": "close",
                  "最高": "high", "最低": "low",
                  "成交量": "volume", "成交额": "amount"}
        df = df.rename(columns=rename)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        cols = ["open", "high", "low", "close", "volume", "amount"]
        return df[[c for c in cols if c in df.columns]]

    def index_components(self, index_code: str,
                         as_of_date: Optional[str] = None) -> list[str]:
        # ⚠️ akshare 不支持时点成分，as_of_date 被忽略 → 幸存者偏差风险
        # 生产回测请用 tushare 源。先告警再 import akshare，避免未装包时 import 先抛错。
        if as_of_date:
            import warnings
            warnings.warn(
                "akshare 不支持时点成分股(as_of_date)，结果含幸存者偏差，"
                "历史回测请改用 source='tushare'",
                stacklevel=2,
            )
        import akshare as ak
        mapping = {"000300": "000300", "000905": "000905", "000016": "000016"}
        sym = mapping.get(index_code)
        if not sym:
            raise ValueError(f"akshare 暂不支持指数 {index_code}")
        df = ak.index_stock_cons(symbol=sym)
        # 列名兼容：品种代码 / 成分券代码
        col = "品种代码" if "品种代码" in df.columns else df.columns[0]
        return df[col].astype(str).tolist()


def _norm(s: str) -> str:
    return s.replace("-", "")
