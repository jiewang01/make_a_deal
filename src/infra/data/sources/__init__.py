"""数据源实现。导入即注册（@register_source 自动注册到注册表）。"""
from . import akshare_source  # noqa: F401  注册 akshare
from . import tushare_source  # noqa: F401  注册 tushare
