# Changelog

本文件记录 make_a_deal 所有版本变更。遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [SemVer](https://semver.org/lang/zh-CN/)。

> 每个版本均经 **Defender / Attacker / Judge 三角色对抗** 评审（见 [blueprint.md](./blueprint.md) 开发铁律）。

---

## [Unreleased]

### Added
- 开发铁律：Defender / Attacker / Judge 三角色对抗约束（写入 blueprint.md）
- 版本化实施计划路线图 v0.1.0 → v1.0.0（blueprint.md 第八章）
- blueprint.html 交互式架构图（系统暗色自适应）
- code_plan.html 实施计划可视化（版本路线图 + 三角色对抗循环）
- changelog.md 版本管理机制

### Pending（下一版 v0.4.0）
- L2 因子库 + 清洗：Alpha101(部分公式) + factor_cleaner(去极值/标准化/行业+市值中性化)
- Attacker 重点：中性化错误、异常值、财报披露日 vs 报告期

---

## [v0.1.0] - 2026-08-28

### Added
- 仓库初始化：目录骨架（src/infra·primitives·agent·memory·governance·execution·tools 各层 `__init__.py`）
- `.gitignore`（Python 规则）、`requirements.txt`（pandas/akshare/tushare/pandas-ta 等）
- `blueprint.md` / `blueprint.html` 架构设计（四层 + 双闭环 + 三角色对抗）
- `changelog.md` 版本管理
- `.trae/skills/make_a_deal/SKILL.md`（日线 A 股量化全流程）

### Defender 自检
- 目录骨架可导入、CI 配置就绪、约束文档完整

### Attacker 攻击（≥3）
- v0.1.0 仅地基版本，无业务逻辑，攻击面在后续版本展开
- 已记录待验：CI 是否真正拦截三角色缺失的 PR（待 v0.2.0 验证）

### Judge 裁决
- ✅ 通过：作为地基版本，约束与骨架就位，允许后续版本展开对抗

---

## [v0.2.0] - 2026-08-28

### Added
- L1 数据层 `src/infra/data/`：多源注册表（装饰器 `@register_source`）+ AKShare/Tushare 双源 + Parquet 缓存 + 增量更新
- 股票池 `src/infra/universe/`：CSI300/500/50，支持时点成分 `as_of_date`（防幸存者偏差）
- 配置层 `src/infra/config/`：YAML 驱动 + `config/data.yml`
- 经验库 `src/memory/experience_store/`：JSONL，Attacker 攻击点沉淀
- 测试 `tests/test_data_loader.py`：9 离线 + 1 在线，覆盖复权隔离/时点告警/缓存污染等

### Defender 自检
- 9/9 离线测试通过（`pytest -m "not online"`）

### Attacker 攻击（≥3，均已修复）
1. **复权基准漂移**（阻断）：qfq/hfq 以最新交易日为基准，增量拼接致价格跳变 → adj!=none 禁用增量全量重拉
2. **tushare NameError**（阻断）：daily 用 ts.pro_bar 但 ts 为 __init__ 局部变量 → 存 self._ts
3. **幸存者偏差默认值**（高）：get_universe 默认 as_of_date=None → 未传告警
4. **缓存污染**（中）：增量写回未裁旧缓存未来数据 → 读后先按 as_of_date 裁剪
5. **index 健壮性**（中）：source 返回非 datetime index 时崩 → new.index=pd.to_datetime 防御

### Judge 裁决
- ✅ 通过：5 个攻击点全修复且有测试覆盖，经验库已沉淀（records.jsonl）

---

## [v0.3.0] - 2026-08-28

### Added
- L2 回测引擎 `src/primitives/backtest/`：
  - `engine.py`：事件驱动四阶段（Market→Signal→Order→Fill）+ lot 级持仓（T+1）+ 拒单记录 + 绩效指标
  - `ashare_rules.py`：A 股规则完整建模（佣金万2.5最低5元/印花税卖出千0.5/过户费双向万0.1/滑点/涨跌停板价/100股整手）
  - `strategy.py`：Strategy 基类 + MACross 均线交叉策略 + Signal
- 前视偏差防护：t 日收盘生成信号，t+1 开盘成交；策略只见截至 t 的数据；最后一日不发信号
- 测试 `tests/test_backtest.py`：16 离线用例 + 5 Attacker 回归用例

### Defender 自检
- 30/30 测试通过（`pytest -m "not online"`，含 v0.2.0 数据层 9 个）
- MACross 端到端：合成 V 形行情先买后卖成交顺序正确

### Attacker 攻击（≥3，均已修复）
1. **滑点穿透涨跌停**（阻断）：open=10.99 未触板(涨停 11.0)，滑点价 10.99×1.001=11.00089 > 涨停价违反价格笼子 → 成交价 clamp 至 [跌停, 涨停] 板内
2. **win_rate 漏算买入费用**（高）：realized pnl 只扣卖出费用，买入佣金/过户费未入 round-trip → Lot 增 fee_per_share 均摊，reduce_fifo 返回含费成本价
3. **期末敞口不可见**（中）：持仓未平时 metrics 无期末股数/市值，浮盈亏被遗漏 → 增 open_position_shares / open_position_value
4. **短样本年化夏普误导**（中）：3 根 bar 算出年化数千倍 → n < 63 交易日置 None

### Judge 裁决
- ✅ 通过：4 个攻击点全修复且有回归测试；前视/T+1/涨跌停/费用四攻击面均有正向用例；经验库沉淀 4 条（累计 9 条）

---

## 计划中版本（Planned）

> 仅列计划，未发布。发布时移入上方对应 `[vX.Y.Z]` 区块。

### [Planned v0.4.0] L2 因子库 + 清洗
- Alpha101（部分公式）+ factor_cleaner（去极值/标准化/行业+市值中性化）
- Attacker 重点：中性化错误、异常值、财报披露日 vs 报告期

### [Planned v0.5.0] L2 组合风控 + 择时
- 8 层风控 + 止损（ATR/追踪）+ 市场择时
- Attacker 重点：风控穿透、极端行情、仓位溢出、止损跳空

### [Planned v0.6.0] L2 回测增强
- Brinson 归因 + WFO 滚动前进 + 子区间稳定性（三层验证雏形）
- Attacker 重点：过拟合、参数泄漏、样本外崩塌

### [Planned v0.7.0] L3 Agent 最小闭环
- planner + tool_caller + 四阶闭环（无记忆库）
- Attacker 重点：幻觉、越权、无限循环、token 失控

### [Planned v0.8.0] L3 偏差校正 + 记忆库
- bias_correction + factor/strategy/experience store
- Attacker 重点：经验库污染、误丢弃、相似度误判

### [Planned v0.9.0] L4 治理 + 沙箱
- sandbox（Docker/子进程隔离）+ audit + human_interface
- Attacker 重点：沙箱逃逸、越权、审核绕过

### [Planned v1.0.0] MCP 工具化 + 端到端
- tools/ MCP 暴露 + dashboard + 端到端 demo + 文档
- Attacker 重点：全量回归、接口契约、安全

---

## 版本裁决记录模板（每版 Judge 填写）

```
版本：vX.Y.Z
Defender 自检：[✅/❌] 关键实现：...
Attacker 攻击（≥3）：1)... 2)... 3)...
Judge 裁决：[通过 / 打回] 理由：...
经验库沉淀：[条目路径] / [无]
git tag：vX.Y.Z
```
