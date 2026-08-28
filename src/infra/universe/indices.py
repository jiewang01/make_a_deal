"""指数成分股 universe：CSI300 / CSI500 / SSE50。

防幸存者偏差（Attacker 攻击面）：
- 支持时点成分股（as_of_date），历史回测必须用当时成分，含已退市样本。
- akshare 源不支持时点成分（有偏差），生产回测强烈建议用 tushare 源
  （index_weight 接口返回时点成分含退市）。
- 调用 akshare + as_of_date 会显式告警，提示换源。
"""
from __future__ import annotations
from typing import Optional

from ..data.base import get_source

# universe 名 -> 指数代码
SUPPORTED = {"csi300": "000300", "csi500": "000905", "sse50": "000016"}


def get_universe(name: str = "csi300", *,
                 source: str = "akshare",
                 as_of_date: Optional[str] = None) -> list[str]:
    """取指数成分股列表。

    Args:
        name: csi300 / csi500 / sse50。
        source: 数据源。历史回测+as_of_date 建议 tushare（防幸存者偏差）。
        as_of_date: 时点 "YYYY-MM-DD"。历史回测必传，取当时成分含退市。
    Returns:
        成分股代码列表（纯数字，不带交易所后缀）。
    """
    if name not in SUPPORTED:
        raise ValueError(f"universe '{name}' 不支持，可选: {list(SUPPORTED)}")
    # 修复 A3: 未传 as_of_date 默认拿最新成分，含幸存者偏差，告警强制调用方思考
    if as_of_date is None:
        import warnings
        warnings.warn(
            "未传 as_of_date，使用最新成分股，含幸存者偏差；"
            "历史回测务必传 as_of_date 并用 source='tushare'",
            stacklevel=2,
        )
    src = get_source(source)
    codes = src.index_components(SUPPORTED[name], as_of_date=as_of_date)
    # 规范化：去交易所后缀（600519.SH -> 600519）
    return [c.split(".")[0] for c in codes]
