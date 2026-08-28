# make_a_deal

> 日线级别 A 股量化分析 · AI 驱动

LLM 是研究员，框架是研究工具箱 + 实验台 + 记忆库。任何变更经 **Defender / Attacker / Judge** 三角色对抗（见 [blueprint.md](./blueprint.md)）。

## 当前版本

**v1.0.0** — L1-L4 四层架构全部就位

| 层 | 版本 | 能力 |
|----|------|------|
| L1 数据层 | v0.2–v0.3 | 多源行情（akshare/tushare）+ Parquet 缓存 + 增量 + universe + 因子库（Alpha101）+ 清洗 |
| L2 回测层 | v0.4–v0.5 | 事件驱动回测 + A 股规则（涨跌停/T+1/手续费）+ WFO 滚动前进验证 + Brinson 归因 + 风控闸门 + 止损链 |
| L3 Agent 层 | v0.6–v0.8 | 四阶闭环（假设→验证→解读→迭代）+ 失控防护 + 偏差校正 + 记忆库（MemoryStore/因子策略查重/经验自学习） |
| L4 治理层 | v0.9 | 沙箱隔离执行 + 审计卡点 + 人审接口 |
| 工具化 | v1.0 | MCP 工具封装 + 端到端 pipeline + Dockerfile |

## 目录

```
src/
├── infra/                  # L1 数据层
│   ├── data/sources/       #   akshare/tushare 行情源
│   ├── data/               #   Parquet 缓存 + 增量
│   ├── universe/           #   CSI300/500/50 股票池
│   └── config/             #   YAML 配置加载
├── primitives/             # L1-L2 原语
│   ├── factors/             #   Alpha101 因子 + 清洗 + 算子
│   ├── backtest/            #   事件驱动引擎 + A股规则 + WFO + 归因
│   └── risk/                #   风控闸门 + 止损链 + 择时
├── agent/                   # L3 Agent 层
│   ├── loop.py              #   四阶闭环执行器
│   ├── planner.py           #   计划生成 + 校验
│   ├── tool_registry.py     #   白名单工具注册表
│   └── bias_correction.py   #   偏差检测（近因/确认/空证据/过拟合）
├── memory/                  # L3 记忆库
│   ├── stores.py            #   MemoryStore 基座（原子写/指纹去重/相似检索）
│   ├── factor_strategy_stores.py  # 因子/策略注册前查重
│   └── experience_store/    #   Attacker 经验库（失败案例自学习）
├── governance/              # L4 治理层
│   ├── sandbox.py           #   子进程隔离（禁网/禁写/import白名单）
│   ├── audit.py             #   入库前审计卡点
│   └── human_interface.py   #   人审闸门 + 目标校验
└── tools/                   # v1.0 MCP 工具封装
    ├── mcp_server.py        #   MCPServer（注册→校验→序列化）
    └── pipeline.py           #   端到端 pipeline demo
config/data.yml
tests/                       # 197 项离线测试
Dockerfile
```

## 安装

```bash
pip install -r requirements.txt
export TUSHARE_TOKEN=xxx   # 可选，用 tushare 时点成分防幸存者偏差
```

## 快速开始

### 端到端 Pipeline（合成数据，离线）

```python
from src.tools import run_pipeline

res = run_pipeline()
print(res.summary())
# ✅ 数据加载 → ✅ 因子计算 → ✅ 回测 → ✅ 风控 → ✅ 偏差校正 → ✅ 审计入库
```

### MCP 工具服务

```python
from src.tools import MCPServer

srv = MCPServer()
srv.register("backtest", my_backtest_fn,
             desc="回测策略",
             input_schema={"strategy": {"type": "str", "required": True}})

# 列出工具（MCP tools/list 格式）
print(srv.list_tools())

# 调用工具（输出 JSON 安全）
result = srv.call("backtest", {"strategy": "ma_cross"})
print(result.ok, result.value)

# JSON 字符串响应（MCP 工具调用格式）
print(srv.call_json("backtest", {"strategy": "ma_cross"}))
```

### 回测单标的

```python
from src.primitives.backtest.engine import BacktestEngine
from src.primitives.backtest.strategy import MACross

# df: OHLCV DataFrame, index=日期
engine = BacktestEngine(df, MACross(fast=5, slow=20), cash=1_000_000, code="600519")
result = engine.run()
print(result.metrics)  # total_return, sharpe, max_drawdown, ...
```

### 因子计算

```python
from src.primitives.factors.alpha101 import compute_all

factors = compute_all(panel_df)  # MultiIndex(date, code) OHLCV
# 返回 DataFrame: 每列一个 Alpha101 因子
```

### 沙箱执行用户代码

```python
from src.governance import Sandbox, SandboxConfig

sb = Sandbox(SandboxConfig(timeout=10))
res = sb.run("import numpy as np; print(np.array([1,2,3]).sum())")
# os/subprocess/socket 被禁，open() 限工作目录，eval/exec 移除
```

### 审计 + 人审

```python
from src.governance import AuditTrail, HumanGate, Decision

trail = AuditTrail("audit.jsonl")
trail.submit("strategy", "ma_cross", "MA5>MA20",
             evidence={"sharpe": 1.5})   # 无证据拒绝
trail.approve("ma_cross", "MA5>MA20", "reviewer_A")

gate = HumanGate("gate.jsonl")
req_id = gate.request_review("register_strategy", "ma_cross")
gate.decide(req_id, Decision.APPROVE, "user_A")  # 默认 deny
gate.is_approved(req_id)  # True
```

## 测试

```bash
pytest                    # 离线逻辑测试（197 项）
pytest -m online          # 在线集成测试（需网络 + akshare）
```

## Docker

```bash
docker build -t make_a_deal .
docker run make_a_deal    # 自动跑 pytest -m "not online"
```

## 开发流程

所有变更经 **Defender / Attacker / Judge** 三角色对抗：

1. **Defender** 实施 + 单元测试
2. **Attacker** 攻击 ≥3 问题（沙箱逃逸/越权/审核绕过/接口契约/…）
3. **Defender** 修复 + 回归测试
4. **Judge** 裁决 → 经验库沉淀 → changelog → commit/tag/push

经验库累计 40 条失败案例（v0.2–v1.0），新因子/策略生成前查重避免重蹈覆辙。

## 版本历史

详见 [changelog.md](./changelog.md)。架构设计见 [blueprint.md](./blueprint.md)。
