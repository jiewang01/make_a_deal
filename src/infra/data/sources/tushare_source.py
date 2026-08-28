"""Tushare 数据源（需 token，支持时点成分股，防幸存者偏差）。

注意（Attacker 关注点）：
- 复权：pro.daily 返回不复权；前/后复权需用 pro_bar(adj=...)。
  本实现 adj != "none" 时调 pro_bar，失败则回退不复权并告警。
- 时点成分：index_weight(trade_date=...) 返回该日权重成分，含已退市样本，
  是防幸存者偏差的关键接口。
"""
from __future__ import annotations
import os
import warnings
from typing import Optional
import pandas as pd

from ..base import DataSource, register_source


@register_source("tushare")
class TushareSource(DataSource):
    """Tushare pro 数据源。"""

    def __init__(self, token: Optional[str] = None):
        token = token or os.getenv("TUSHARE_TOKEN")
        if not token:
            raise RuntimeError(
                "Tushare 需要 token：设环境变量 TUSHARE_TOKEN 或传 token 参数"
            )
        import tushare as ts
        ts.set_token(token)
        self.pro = ts.pro_api()
        self._ts = ts  # 保存供 daily 调 pro_bar（修复 A2: 原 ts 为局部变量致 NameError）

    def daily(self, code: str, start: str, end: str, adj: str = "qfq") -> pd.DataFrame:
        ts_code = self._to_ts_code(code)
        s, e = _norm(start), _norm(end)
        if adj == "none":
            df = self.pro.daily(ts_code=ts_code, start_date=s, end_date=e)
        else:
            try:
                df = self._ts.pro_bar(ts_code=ts_code, start_date=s, end_date=e,
                                      adj=adj)
            except Exception as exc:  # 复权失败回退
                warnings.warn(f"tushare pro_bar({adj}) 失败回退不复权: {exc}",
                              stacklevel=2)
                df = self.pro.daily(ts_code=ts_code, start_date=s, end_date=e)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index("trade_date").sort_index()
        return df

    def index_components(self, index_code: str,
                         as_of_date: Optional[str] = None) -> list[str]:
        # 关键：支持时点成分，防幸存者偏差
        td = _norm(as_of_date) if as_of_date else None
        df = self.pro.index_weight(index_code=index_code, trade_date=td)
        if df is None or len(df) == 0:
            return []
        return df["con_code"].unique().tolist()

    @staticmethod
    def _to_ts_code(code: str) -> str:
        """本地代码 -> tushare ts_code：600519->600519.SH, 000001->000001.SZ。

        沪市：60/68/90 开头；深市：00/30/20(转债除外) 开头；北交所：8/4 开头。
        """
        if code.startswith(("60", "68", "90")):
            return f"{code}.SH"
        if code.startswith(("8", "4")):
            return f"{code}.BJ"
        return f"{code}.SZ"


def _norm(s: str) -> str:
    return s.replace("-", "")
