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

## [v0.4.0] - 2026-08-28

### Added
- L2 因子库 `src/primitives/factors/`：
  - `operators.py`：Alpha101 风格时序算子（delay/delta/sign/ts_corr/ts_cov/ts_std/ts_rank），仅向后看无前视
  - `alpha101.py`：Alpha101 子集 alpha006/012/013/044（日线 OHLCV 可算公式），`@register_factor` 注册表 + `compute_all`
  - `cleaner.py`：factor_cleaner 清洗流水线（**按日横截面口径**）：MAD 去极值 → z-score → 行业+市值中性化（每日横截面 OLS）+ `cs_rank` + `clean_pipeline`
  - `align_fundamental`：财报按披露日（ann_date）merge_asof 对齐到交易日，防报告期前视
- 测试 `tests/test_factors.py`：19 离线用例（含截断重算无前视判据、横截面正交性、A1–A5 回归）

### Defender 自检
- 42/42 通过（实施期）；修复后 49/49 全量通过（含 v0.2/v0.3 回归）

### Attacker 攻击（≥3，均已修复）
1. **清洗统计量前视**（阻断）：winsorize/zscore 按列全时序统计，t 日因子用了 t+1..T 数据 → 全部改按日横截面，新增"截断重算不变性"无前视判据测试
2. **中性化退化**（阻断）：按列回归中行业/市值为常数 X，回归退化为去均值，暴露完全未剔除 → 改每日横截面 OLS（行业哑变量 + 截距 + log 市值）取残差
3. **NaN 污染整列**（高）：一条 NaN 经 lstsq 得 NaN beta 污染当日全部残差，且丢弃无报告 → 按日有效样本回归，缺失位 NaN，丢弃比例超 30% 告警
4. **log(非正市值) 崩溃**（中）：mktcap 含 0/负 → -inf 污染回归 → 剔除并告警
5. **披露日工具缺失**（高）：未来接 PE/ROE 按报告期对齐即前视 → 新增 align_fundamental（ann_date merge_asof backward）
6. **中性化缺截距**（中，修复中自发现）：回归无截距致因子整体水平暴露残留、残差均值≠0 → X 加 const 列

### Judge 裁决
- ✅ 通过：6 个攻击点全修复且有回归测试；"截断重算不变性"作为因子无前视的机械化判据入库；经验库沉淀 6 条（累计 15 条）

---

## [v0.5.0] - 2026-08-28

### Added
- L2 组合风控 + 择时 `src/primitives/risk/`：
  - `risk_gate.py`：日线 5 层风控闸门（L1 总仓位等比缩放 / L2 单票截断 / L3 行业组内缩放 / L4 流动性参与率 + 停牌禁增不禁持 / L5 终检硬断言）+ GateResult 违规记录
  - `stops.py`：止损链（百分比 / ATR / 追踪，取最深触发）+ 跳空缺口规则 + ATR(n)
  - `timing.py`：MATiming 均线趋势择时（exit_on_off 可选强制清仓）
- 测试 `tests/test_risk.py`：23 离线用例（含风控只缩不放、终检穿透、缺口规则、停牌语义）

### Defender 自检
- 实施期自检即捕获 2 个真实 bug（流动性公式反向、跳空缺口方向反）并修复
- 修复后 72/72 全量通过（含 v0.2–v0.4 回归）

### Attacker 攻击（≥3，均已修复）
1. **流动性公式反向**（阻断）：w_max=(参与率×组合值)/成交额量纲错误，流动性充裕的票反而被重裁 → 改为 参与率×当日成交额/组合值
2. **跳空缺口方向反**（阻断）：多头止损退出价 max(stop, next_open) 假装跳空低开仍按止损价成交 → 改 min，缺口损失显式化
3. **入口日止损伪触发**（高）：止损基准当日才成立，入口日 low 击穿即触发 → check 增 is_entry_day 豁免
4. **停牌强制清零**（高）：无成交额把存量持仓权重置零 = 假设停牌能卖出 → 禁增不禁持（holdings 参数，保留存量并告警）
5. **择时空序列崩溃**（中）：MATiming 空 Series IndexError → 入口判空保守 False
6. **止损参数无校验**（中）：pct/trail 负数或 >1 静默失效 → 构造期校验开区间

### Judge 裁决
- ✅ 通过：6 个攻击点全修复且有回归测试；风控"只缩不放"不变量 + L5 终检硬断言构成机械化防穿透判据；经验库沉淀 6 条（累计 21 条）

---

## [v0.6.0] - 2026-08-28

### Added
- L2 回测增强 `src/primitives/backtest/`（三层验证雏形）：
  - `attribution.py`：Brinson-BHB 归因（行业 配置/选股/交互 三分解 + 恒等式对账硬校验 + 现金显式建模 + 权重和/行业标签/收益缺口防御）
  - `wfo.py`：WFO 滚动前进验证（fold 切片防泄漏硬断言 + embargo 间隔 + NaN 候选不参与选参 + IS/OOS 衰减比过拟合警报）
  - `stability.py`：子区间稳定性（等长切段 + 链式基准段收益复合恒等 + 崩塌/占比/离散度三判据）
- 测试 `tests/test_enhance.py`：24 离线用例

### Defender 自检
- 96/96 全量通过（含 v0.2–v0.5 回归）

### Attacker 攻击（≥3，均已修复）
1. **WFO 评估异常被静默吞**（高）：evaluate 抛异常转 NaN，实现 bug 与"不可评估"混淆且无报告 → 告警 + n_eval_errors 计数区分
2. **过拟合判据忽略 OOS 为负**（高）：IS=-0.2/OOS=-0.3 时 decay=1.5 无警报但样本外实亏 → OOS 加权均值 <0 并入 overfit_warning
3. **子区间段长不均偏置**（中）：len%k≠0 时 array_split 段长差 1，短段波动被放大 → 等长切段（丢弃开头余数）
4. **k 接近 len 伪稳定**（中）：k=len 每段 1 点恒 0 收益恒判稳定 → 强制 len ≥ 2k

### Judge 裁决
- ✅ 通过：4 个攻击点全修复且有回归测试；Brinson 恒等式对账 + WFO fold 防泄漏断言构成机械化判据；经验库沉淀 4 条（累计 25 条）

---

## [v0.7.0] - 2026-08-28

### Added
- L3 Agent 最小闭环 `src/agent/`（LLM 无关设计，可注入 callable）：
  - `tool_registry.py`：白名单工具注册表（同名重复注册报错防覆盖）+ 参数 schema 校验（含 schema 外参数拒绝）+ 结果截断 + 异常转结构化错误
  - `planner.py`：LLMPlanner（注入 llm callable + prompt 模板）/ ScriptedPlanner（测试复现）+ parse_plan（markdown 围栏容错）+ validate_plan（工具白名单/参数/步数硬校验）
  - `loop.py`：四阶闭环（假设→验证→解读→迭代）+ 失控防护（max_iterations / max_total_calls 预算 / 停滞检测 / 观测截断）+ 全程 trace（StepRecord/IterationRecord）
- 测试 `tests/test_agent.py`：23 离线用例（越权/幻觉/死循环/预算/截断全覆盖）

### Defender 自检
- 119/119 全量通过（含 v0.2–v0.6 回归）

### Attacker 攻击（≥3，均已修复）
1. **非 str 结果绕过 token 截断**（阻断）：dict/list 结果全量 repr 进入观测 → `_step_observation` 统一 400 字符截断
2. **预算截断丢弃已有终答**（高）：末步预算耗尽时含 final_answer 的计划误报 budget_exceeded → 截断后仍检查终答，completed 上报 + 保留截断记录
3. **stall_tolerance=0 恒判停滞**（高）：`[-1:]` 单元素 all() 恒 True → 构造期校验 ≥1
4. **schema 外参数依赖 TypeError 兜底**（高）：LLM 塞越权参数未在计划期拦截 → validate_args 显式拒绝 + parse_plan 双层拦截

### Judge 裁决
- ✅ 通过：4 个攻击点全修复且有回归测试；白名单 + 双层参数校验构成越权/幻觉机械化拦截；经验库沉淀 4 条（累计 29 条）

---

## [v0.8.0] - 2026-08-28

### Added
- L3 偏差校正 + 记忆库 `src/memory/` + `src/agent/bias_correction.py`：
  - `stores.py`：MemoryStore 基座——JSONL 原子写（fsync + tempfile/os.replace 重写）、内容指纹去重（易变字段不参与）、损坏行跳过不传染、关键词/Jaccard 相似度检索（阈值 [0,1] 硬校验）、purge 半数护栏（防 predicate 写错整库误删）
  - `factor_strategy_stores.py`：FactorStore/StrategyStore 注册前查重——同名/同公式命中 + 相似度命中按记录身份合并去重（DuplicationCheck）
  - `experience_store`：重构为 MemoryStore 架构（原子写 + 攻击记录指纹去重防污染）
  - `bias_correction.py`：闭环后偏差检测——recency_bias（近因偏差，结论引用早期证据则豁免）/ confirmation_bias（仅成功重复调用，失败重试豁免）/ empty_evidence（幻觉警报）/ overfit_risk（IS/OOS 衰减 + 样本外实亏），BiasCheckResult 只给证据与建议不篡改结论
- 测试 `tests/test_memory_bias.py`：21 离线用例（去重/检索/purge 护栏/四类偏差/Attacker 回归）

### Defender 自检
- 140/140 全量通过（`pytest -m "not online"`，含 v0.2–v0.7 回归；1 个 akshare 在线用例因沙箱无依赖被 deselect）

### Attacker 攻击（≥3，均已修复）
1. **经验库污染**（阻断）：experience_store 裸 open/write，无原子写无去重，重复攻击记录与写坏行直接入库 → 基于 MemoryStore 重构（fsync + 指纹去重 + 损坏行跳过）
2. **相似度阈值越界静默误判**（高）：search_similar threshold<0 全量命中、>1 永不命中且无报错 → 构造期 [0,1] 校验 ValueError
3. **查重列表重复计数**（中）：同名命中(1.0)与相似度命中指向同一条记录时 similar 列表重复列出 → 按记录 JSON 身份去重取最高分
4. **确认偏差语义混淆**（中）：失败重试被计入确认偏差（重试是纠错不是堆证据）→ 只统计成功步骤签名

### Judge 裁决
- ✅ 通过：4 个攻击点全修复且有回归测试；purge 半数护栏 + 阈值硬校验 + 指纹去重构成记忆库三道机械化防线；经验库沉淀 4 条（累计 33 条）

---

## [v0.9.0] - 2026-08-28

### Added
- L4 治理层 `src/governance/`：
  - `sandbox.py`：子进程隔离执行 Python 代码——预导入第三方库（numpy/pandas/scipy 在 guard 前完成 init）→ import 白名单 guard（os/subprocess/socket/pathlib 等高危模块直接拒绝）→ restricted builtins（移除 eval/exec/compile/breakpoint/input/globals/locals/vars，open 替换为工作目录限制版 _safe_open，路径穿越拦截）→ CPU/内存/超时硬上限
  - `audit.py`：入库前审计卡点——AuditTrail append-only 审计轨迹（submit→approve/reject→revoke 全生命周期），无 evidence 提交直接拒绝（防拍脑袋通过），同 artifact 不同状态不被互相去重（status 参与指纹）
  - `human_interface.py`：人类监督接口——InvestmentGoal 目标 schema 校验（收益/回撤范围+股票池上限 500）+ HumanGate 人审闸门（默认 deny，approve/reject/defer/revoke 全生命周期，决策不可重复）
- 测试 `tests/test_governance.py`：34 离线用例（沙箱 8+审计 8+人审 5+目标 4+Attacker 回归 9）

### Defender 自检
- 174/174 全量通过（`pytest -m "not online"`，含 v0.2–v0.8 回归；1 个 akshare 在线用例 deselect）

### Attacker 攻击（≥3，均已修复）
1. **沙箱 open() 逃逸**（阻断）：open() 内置函数未拦截，用户代码可读写工作目录外任意文件（/etc/passwd 等）→ restricted builtins 替换 open 为 _safe_open（仅允许工作目录内路径，路径穿越一并拦截）
2. **沙箱 builtins 暴露**（高）：exec 命名空间默认暴露全部 builtins（eval/exec/compile/__import__），可绕过 import guard → 构建 _safe_builtins 字典移除危险内置函数，__import__ 替换为 guard 版
3. **人审决策不可撤回**（中）：HumanGate approve 后无 revoke，错误审核无法回滚 → 新增 revoke 方法（append-only，撤回后 is_approved=False）
4. **股票池无上限**（中）：InvestmentGoal.stock_pool 无数量校验，万级股票池致资源耗尽 → validate 增上限 500

### Judge 裁决
- ✅ 通过：4 个攻击点全修复且有回归测试；restricted builtins + import 白名单 + 工作目录限制 open 构成沙箱三道机械化防线；审计/人审 append-only + revoke 全生命周期构成治理闭环；经验库沉淀 4 条（累计 37 条）

---

## [v1.0.0] - 2026-08-28

### Added
- MCP 工具封装层 `src/tools/`：
  - `mcp_server.py`：MCPServer 工具服务——白名单注册→JSON Schema 参数校验→执行→`_to_jsonable` 递归序列化（numpy/pandas → list/dict）→不可序列化拒绝；`list_tools()` 输出 MCP tools/list 格式；`call_json()` 输出 MCP 工具调用响应
  - `pipeline.py`：端到端 pipeline（合成数据→因子→回测→风控→偏差校正→审计），任一步骤失败终止并返回失败原因（防错误级联）；支持人审模式（HumanGate）
- `Dockerfile`：python:3.12-slim + 依赖层缓存 + pytest 入口
- 测试 `tests/test_tools.py`：23 离线用例（MCP 10+序列化 5+Pipeline 4+Attacker 回归 4）

### Defender 自检
- 197/197 全量通过（`pytest -m "not online"`，含 v0.2–v0.9 回归；1 个 akshare 在线用例 deselect）
- Pipeline 端到端 demo 验证通过：数据→因子→回测→风控→偏差校正→审计 全链路 OK

### Attacker 攻击（≥3，均已修复）
1. **审计硬编码通过**（高）：Pipeline 的 submit/approve 返回值未检查，audit_approved 硬编码 True，重复运行时 submit dedup 失败仍报告成功 → 检查 submit 返回值，失败时查 is_approved 判断是否已审核通过，approve 返回值直接赋给 audit_approved
2. **Pipeline 路径注入**（中）：audit_path/gate_path 无路径校验，可通过 `../..` 写入任意文件系统位置 → 入口添加 normpath 校验，拒绝含 `..` 的路径
3. **call_json default=str 掩盖**（低）：`json.dumps(default=str)` 将不可序列化类型静默转为字符串，与 call() 的拒绝行为不一致 → 移除 default=str，与 call() 保持一致

### Judge 裁决
- ✅ 通过：3 个攻击点全修复且有回归测试；MCPServer 复用 ToolRegistry 白名单 + schema 校验 + _to_jsonable 三道机械化防线构成接口契约保障；Pipeline 逐步失败终止 + 路径校验 + 审计返回值检查构成端到端安全链；经验库沉淀 3 条（累计 40 条）
- 🎉 v1.0.0 是首个正式发布版本（1.0.0），L1-L4 四层架构全部就位，197 项离线测试全通过

---

## 计划中版本（Planned）

> v1.0.0 已完成全部 L1-L4 四层架构实施。后续版本可基于 MCP 工具化向外扩展。

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
