"""MCP 工具封装层（v1.0.0）：对外暴露 + 端到端 pipeline。

子模块：
- mcp_server: MCP 工具服务（注册→schema 校验→JSON 序列化输出）
- pipeline: 端到端 pipeline（数据→因子→回测→风控→偏差→审计）
"""
from .mcp_server import MCPServer, MCPToolDef, _to_jsonable
from .pipeline import run_pipeline, PipelineResult, PipelineStep, make_synthetic_panel

__all__ = [
    "MCPServer", "MCPToolDef", "_to_jsonable",
    "run_pipeline", "PipelineResult", "PipelineStep", "make_synthetic_panel",
]
