"""数据源基类与注册表。

设计要点（对应 Attacker 量化专项攻击面，见 blueprint.md）：
- 复权一致性：所有行情接口必须显式传 adj（qfq/hfq/none），
  缓存 key 含 adj，不同复权物理隔离，绝不混用。
- 前视偏差防护：接口签名支持 as_of_date，实现不得返回晚于该日的数据。
- 退市样本：index_components 支持 as_of_date，返回该时点成分（含已退市），
  防幸存者偏差。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional
import pandas as pd

# 数据源注册表：name -> DataSource 子类
_SOURCE_REGISTRY: dict[str, type["DataSource"]] = {}


def register_source(name: str):
    """数据源装饰器注册表：@register_source("akshare") 自动注册，可插拔。

    扩展新数据源无需改核心代码，符合 quant-trading-system 的装饰器注册模式。
    """
    def deco(cls):
        _SOURCE_REGISTRY[name] = cls
        cls.name = name
        return cls
    return deco


def get_source(name: str) -> "DataSource":
    """按名取数据源实例。未注册抛 KeyError。"""
    if name not in _SOURCE_REGISTRY:
        raise KeyError(f"数据源 '{name}' 未注册，已注册: {list(_SOURCE_REGISTRY)}")
    return _SOURCE_REGISTRY[name]()


class DataSource(ABC):
    """数据源抽象基类。子类必须实现 daily 与 index_components。"""

    name: str = ""

    @abstractmethod
    def daily(self, code: str, start: str, end: str, adj: str = "qfq") -> pd.DataFrame:
        """日线行情。

        Args:
            code: 证券代码，如 "600519"（不带交易所后缀，子类内部转换）。
            start/end: "YYYY-MM-DD" 或 "YYYYMMDD"。
            adj: 复权类型 qfq前复权 / hfq后复权 / none不复权。
        Returns:
            DataFrame index=date(Timestamp), columns=[open,high,low,close,volume,amount]
        """

    @abstractmethod
    def index_components(self, index_code: str,
                         as_of_date: Optional[str] = None) -> list[str]:
        """指数成分股。

        Args:
            index_code: 指数代码，如 "000300" 沪深300。
            as_of_date: 时点（"YYYY-MM-DD"）。若提供，返回该日成分（含已退市），
                        防幸存者偏差；None 返回最新成分（有偏差风险）。
        """
