"""数据层：多源行情 + Parquet 缓存 + 增量更新。

公开接口：
- load_daily(code, start, end, source, adj, as_of_date): 加载日线（带缓存+增量）
- get_source(name): 按名取已注册数据源
- register_source(name): 装饰器，注册新数据源
"""
from .loader import load_daily, _cache_path
from .base import get_source, register_source, DataSource

__all__ = ["load_daily", "get_source", "register_source", "DataSource", "_cache_path"]
