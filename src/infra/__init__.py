"""L1 · 基础设施层 Infrastructure。

子模块：
- data: 多源行情 + Parquet 缓存 + 增量更新
- universe: CSI300/500/50 股票池（含退市样本防幸存者偏差）
- config: YAML 配置驱动
- kernel: 计算内核（向量化+事件驱动，后续版本）
"""
