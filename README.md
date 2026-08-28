# make_a_deal

> 日线级别 A 股量化分析 · AI 驱动

LLM 是研究员，框架是研究工具箱 + 实验台 + 记忆库。任何变更经 **Defender / Attacker / Judge** 三角色对抗（见 [blueprint.md](./blueprint.md)）。

## 当前版本

- **v0.2.0** L1 数据层（多源 + Parquet 缓存 + 增量 + universe）
- 路线图见 [code_plan.html](./code_plan.html) / [blueprint.md 第八章](./blueprint.md)

## 目录

```
src/infra/data/        # 多源行情 + Parquet 缓存 + 增量
src/infra/universe/    # CSI300/500/50 股票池
src/infra/config/      # YAML 配置
src/memory/experience_store/  # Attacker 经验库
config/data.yml
tests/
```

## 安装

```bash
pip install -r requirements.txt
export TUSHARE_TOKEN=xxx   # 可选，用 tushare 时点成分防幸存者偏差
```

## 测试

```bash
pytest                    # 离线逻辑测试
pytest -m online          # 在线集成测试（需网络）
```
