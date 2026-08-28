#!/usr/bin/env python3
"""MCP Server 入口：将 make_a_deal 工具暴露为 MCP 协议服务。

供 Trae / Claude Code / Codex 等外部 Agent 通过 MCP 协议调用。

运行方式（stdio 传输）：
    python mcp_server.py

Trae 配置（.trae/mcp.json 或设置界面）：
{
  "mcpServers": {
    "make_a_deal": {
      "command": "python",
      "args": ["/path/to/make_a_deal/mcp_server.py"],
      "env": { "TUSHARE_TOKEN": "your_token" }
    }
  }
}
"""
from __future__ import annotations
import os
import sys

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

from src.tools.mcp_server import MCPServer, _to_jsonable
from src.tools.pipeline import run_pipeline, make_synthetic_panel
from src.primitives.factors.alpha101 import compute_all
from src.primitives.backtest.engine import BacktestEngine
from src.primitives.backtest.strategy import MACross
from src.governance.sandbox import Sandbox, SandboxConfig

mcp = FastMCP("make_a_deal")


# ---------------------------------------------------------------- 工具定义
@mcp.tool()
def run_quant_pipeline() -> str:
    """端到端量化 pipeline：合成数据→因子→回测→风控→偏差校正→审计。
    返回各步骤执行状态和回测指标。
    """
    res = run_pipeline()
    steps = [{"name": s.name, "ok": s.ok, "result": s.result,
              "error": s.error} for s in res.steps]
    return _to_jsonable({
        "ok": res.ok,
        "stop_reason": res.stop_reason,
        "steps": steps,
    })


@mcp.tool()
def compute_alpha_factors(code: str, n_days: int = 250) -> str:
    """计算 Alpha101 因子（使用合成数据演示）。
    Args:
        code: 股票代码（如 600519）
        n_days: 天数（默认 250）
    Returns:
        因子统计摘要 JSON
    """
    panel = make_synthetic_panel(n_days=n_days, n_stocks=1)
    panel = panel.rename(
        {panel.index.get_level_values("code")[0]: code}, level="code")
    factors = compute_all(panel)
    summary = {col: {
        "mean": float(factors[col].mean()),
        "std": float(factors[col].std()),
    } for col in factors.columns}
    return _to_jsonable({"n_factors": len(factors.columns),
                         "summary": summary})


@mcp.tool()
def backtest_ma_cross(code: str, fast: int = 5, slow: int = 20,
                      cash: float = 100000) -> str:
    """均线交叉回测（使用合成数据演示）。
    Args:
        code: 股票代码
        fast: 快线天数（默认 5）
        slow: 慢线天数（默认 20）
        cash: 初始资金（默认 100000）
    Returns:
        回测指标 JSON（total_return, sharpe, max_drawdown, ...）
    """
    panel = make_synthetic_panel(n_days=250, n_stocks=1)
    first_code = panel.index.get_level_values("code").unique()[0]
    px = panel.xs(first_code, level="code")
    strategy = MACross(fast=fast, slow=slow)
    engine = BacktestEngine(px, strategy, cash=cash, code=code)
    result = engine.run()
    return _to_jsonable(result.metrics)


@mcp.tool()
def sandbox_execute(code: str, timeout: int = 10) -> str:
    """在沙箱中安全执行 Python 代码。
    禁止 os/subprocess/socket；open() 限工作目录；eval/exec 移除。
    Args:
        code: Python 代码字符串
        timeout: 超时秒数（默认 10）
    Returns:
        执行结果 JSON（ok, stdout, stderr, exit_code）
    """
    sb = Sandbox(SandboxConfig(timeout=timeout))
    res = sb.run(code)
    return _to_jsonable({
        "ok": res.ok,
        "stdout": res.stdout,
        "stderr": res.stderr,
        "exit_code": res.exit_code,
        "timed_out": res.timed_out,
        "error": res.error,
    })


if __name__ == "__main__":
    mcp.run(transport="stdio")
