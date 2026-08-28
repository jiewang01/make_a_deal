"""MCP 工具封装：将内部能力暴露为 MCP 可调用的标准化工具。

防攻击面设计（v1.0.0）：
- 接口契约：每个工具有 JSON Schema（input/output），输入校验
  + 输出 JSON 序列化（numpy/pandas → list/dict），不可序列化
  类型直接拒绝（防传输层崩溃）；
- 安全：危险操作（代码执行）走 governance.sandbox，不走裸 exec；
- 越权：MCP 工具白名单注册，未注册工具不可调用（复用 ToolRegistry
  白名单 + schema 校验机制）。
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from ..agent.tool_registry import ToolRegistry, ToolCallResult


def _to_jsonable(obj: Any) -> Any:
    """递归将 numpy/pandas 对象转为 JSON 可序列化类型。

    不可序列化 → TypeError（防传输层崩溃）。
    """
    import numpy as np
    # numpy 标量
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    # numpy 数组
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    # pandas DataFrame / Series
    try:
        import pandas as pd
        if isinstance(obj, (pd.DataFrame, pd.Series)):
            return json.loads(obj.to_json(orient="records",
                                          force_ascii=False))
    except ImportError:
        pass
    # 递归处理容器
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    # 基本类型直接返回
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    # 不可序列化 → 拒绝（防传输层崩溃）
    raise TypeError(
        f"结果含不可序列化类型 {type(obj).__name__}，拒绝输出")


@dataclass
class MCPToolDef:
    """MCP 工具定义（JSON Schema 风格）。"""
    name: str
    desc: str
    input_schema: dict = field(default_factory=dict)
    output_desc: str = ""


class MCPServer:
    """MCP 工具服务：注册 → schema 校验 → 执行 → JSON 序列化输出。

    用法：
        srv = MCPServer()
        srv.register("backtest", run_backtest,
                     input_schema={"strategy": {"type": "str", "required": True}})
        result = srv.call("backtest", {"strategy": "ma_cross"})
        print(result.json())  # {"ok": true, "value": {...}}
    """

    def __init__(self):
        self._registry = ToolRegistry()
        self._defs: dict[str, MCPToolDef] = {}

    def register(self, name: str, fn: Callable, *,
                 desc: str = "", input_schema: dict | None = None,
                 output_desc: str = "",
                 max_result_chars: int = 8000) -> None:
        """注册 MCP 工具（白名单制，同名重复报错）。"""
        self._registry.register(
            name, fn, desc=desc,
            arg_schema=input_schema or {},
            max_result_chars=max_result_chars)
        self._defs[name] = MCPToolDef(
            name=name, desc=desc,
            input_schema=input_schema or {},
            output_desc=output_desc)

    def list_tools(self) -> list[dict]:
        """列出所有工具定义（MCP tools/list 响应格式）。"""
        return [
            {
                "name": d.name,
                "description": d.desc,
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        k: {"type": v.get("type", "string")}
                        for k, v in d.input_schema.items()
                    },
                    "required": [k for k, v in d.input_schema.items()
                                 if v.get("required", True)],
                },
                "outputDescription": d.output_desc,
            }
            for d in sorted(self._defs.values(), key=lambda x: x.name)
        ]

    def call(self, name: str, args: dict) -> ToolCallResult:
        """调用工具 → JSON 序列化输出。

        输出保证 JSON 可序列化（不可序列化 → 错误，防传输层崩溃）。
        """
        result = self._registry.call(name, args)
        if result.ok:
            try:
                result.value = _to_jsonable(result.value)
            except (TypeError, ValueError) as e:
                return ToolCallResult(
                    ok=False,
                    error=f"[序列化失败] {e}")
        return result

    def call_json(self, name: str, args: dict) -> str:
        """调用工具并返回 JSON 字符串（MCP 工具调用响应格式）。

        输出保证 JSON 可序列化（与 call() 一致，不可序列化即错误，
        不用 default=str 掩盖问题）。
        """
        result = self.call(name, args)
        return json.dumps({
            "ok": result.ok,
            "value": result.value,
            "error": result.error,
            "truncated": result.truncated,
        }, ensure_ascii=False)
