"""数据层基础测试。

两类测试：
- 离线逻辑测试（默认跑）：验证缓存路径隔离、注册表、universe 映射等纯逻辑。
- 在线集成测试（pytest -m online）：真实拉取，需网络 + akshare/tushare 安装。
"""
from __future__ import annotations
import pytest
import pandas as pd


def test_cache_path_含adj防复权混用():
    """不同 adj 必须落到不同缓存文件，防复权混用（Attacker: 复权错误）。"""
    from src.infra.data.loader import _cache_path
    p_qfq = _cache_path("600519", "qfq")
    p_hfq = _cache_path("600519", "hfq")
    assert p_qfq != p_hfq


def test_未注册数据源报错():
    """未知数据源应明确报错而非静默。"""
    from src.infra.data.base import get_source
    with pytest.raises(KeyError):
        get_source("not_exist")


def test_universe_支持三大指数():
    from src.infra.universe.indices import SUPPORTED
    assert {"csi300", "csi500", "sse50"} <= set(SUPPORTED)


def test_universe_未知名报错():
    from src.infra.universe.indices import get_universe
    with pytest.raises(ValueError):
        get_universe("csi9999")


def test_tushare_ts_code_转换():
    """本地代码转 tushare ts_code 的交易所后缀逻辑。"""
    from src.infra.data.sources.tushare_source import TushareSource
    assert TushareSource._to_ts_code("600519") == "600519.SH"  # 沪市
    assert TushareSource._to_ts_code("000001") == "000001.SZ"  # 深市
    assert TushareSource._to_ts_code("300750") == "300750.SZ"  # 创业板
    assert TushareSource._to_ts_code("688981") == "688981.SH"  # 科创板


def test_akshare_时点成分告警():
    """akshare + as_of_date 应告警幸存者偏差（提示换 tushare）。"""
    import warnings
    from src.infra.data.sources.akshare_source import AKShareSource
    src = AKShareSource()
    # 不实际联网，只验证 as_of_date 触发告警逻辑
    with pytest.warns(UserWarning, match="幸存者偏差"):
        try:
            src.index_components("000300", as_of_date="2020-01-01")
        except Exception:
            # 联网失败也算通过，只要先告警
            pass


@pytest.mark.online
def test_在线拉取_贵州茅台():
    df = _load("600519", "2024-01-02", "2024-01-10")
    assert len(df) > 0
    assert {"open", "close", "high", "low", "volume"} <= set(df.columns)


def _load(code, start, end):
    from src.infra.data.loader import load_daily
    return load_daily(code, start, end, source="akshare", force_refresh=True)


def test_universe_未传as_of_date告警():
    """A3: 未传 as_of_date 应告警幸存者偏差。"""
    import warnings
    from src.infra.universe.indices import get_universe
    with pytest.warns(UserWarning, match="幸存者偏差"):
        try:
            get_universe("csi300")
        except Exception:
            pass  # 联网失败也算通过，只要先告警


def test_复权非none禁用增量全量重拉(monkeypatch, tmp_path):
    """A1: adj=qfq 即使有缓存也全量重拉，防复权基准漂移拼接。

    预期：fetch_start 应为请求 start（全量），而非缓存末尾+1（增量）。
    """
    import pandas as pd
    from src.infra.data import loader as L

    # 假 qfq 缓存
    monkeypatch.setattr(L, "DATA_DIR", tmp_path)
    cached = pd.DataFrame(
        {"open": [1], "high": [1], "low": [1], "close": [1],
         "volume": [1], "amount": [1]},
        index=pd.to_datetime(["2024-01-02"]),
    )
    cached.to_parquet(tmp_path / "600519_qfq.parquet")

    calls = {"start": None}

    class FakeSrc:
        def daily(self, code, start, end, adj="qfq"):
            calls["start"] = start
            return pd.DataFrame(
                {"open": [2], "high": [2], "low": [2], "close": [2],
                 "volume": [2], "amount": [2]},
                index=pd.to_datetime(["2024-01-10"]),
            )

    monkeypatch.setattr(L, "get_source", lambda name: FakeSrc())
    with pytest.warns(UserWarning, match="复权基准"):
        L.load_daily("600519", "2024-01-02", "2024-01-10",
                     source="akshare", adj="qfq")
    # 验证全量重拉：start 应为请求起点，不是缓存末尾+1
    assert calls["start"] == "20240102"


def test_缓存污染_按as_of_date裁剪(monkeypatch, tmp_path):
    """A4: 缓存含未来数据时，传 as_of_date 应裁剪后再写回，不污染缓存。"""
    import pandas as pd
    from src.infra.data import loader as L

    monkeypatch.setattr(L, "DATA_DIR", tmp_path)
    # 假缓存含 as_of_date 之后的数据（模拟污染源）
    cached = pd.DataFrame(
        {"close": [1, 99]},
        index=pd.to_datetime(["2024-01-02", "2024-01-20"]),
    )
    cached.to_parquet(tmp_path / "600519_none.parquet")

    class FakeSrc:
        def daily(self, code, start, end, adj="qfq"):
            return pd.DataFrame(index=pd.to_datetime([]))  # 无新数据，index 仍为 datetime

    monkeypatch.setattr(L, "get_source", lambda name: FakeSrc())
    df = L.load_daily("600519", "2024-01-02", "2024-01-30",
                     source="akshare", adj="none", as_of_date="2024-01-10")
    # 返回不含 as_of_date 之后
    assert (df.index <= pd.Timestamp("2024-01-10")).all()
    # 缓存写回也裁剪了未来数据
    written = pd.read_parquet(tmp_path / "600519_none.parquet")
    assert (written.index <= pd.Timestamp("2024-01-10")).all()
