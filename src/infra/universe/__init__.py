"""股票池 universe。

公开接口：
- get_universe(name, source, as_of_date): 取指数成分股列表
- SUPPORTED: 支持的 universe 名
"""
from .indices import get_universe, SUPPORTED

__all__ = ["get_universe", "SUPPORTED"]
