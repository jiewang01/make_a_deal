"""工具注册表：白名单 + 参数 schema 校验 + 结果摘要。

防攻击面设计（v0.7.0）：
- 越权：只有显式 register 进注册表的工具可被调用（不存在"默认全开"）；
- 幻觉：LLM 编造的工具名/参数在调用前被 schema 拦截，返回结构化错误；
- token 失控：工具返回值超 max_result_chars 时截断并标注（观测膨胀防护）。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable

# 参数类型白名单（够用即可，避免过度工程）
_TYPES = {"str": str, "int": int, "float": float, "bool": bool, "list": list,
          "dict": dict}


@dataclass
class Tool:
    name: str
    desc: str
    fn: Callable[..., Any]
    arg_schema: dict[str, dict] = field(default_factory=dict)  # {参数: {"type": "str", "required": True}}
    max_result_chars: int = 4000


@dataclass
class ToolCallResult:
    ok: bool
    value: Any = None
    error: str = ""
    truncated: bool = False


class ToolRegistry:
    """白名单工具注册表。"""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, name: str, fn: Callable[..., Any], *,
                 desc: str = "", arg_schema: dict[str, dict] | None = None,
                 max_result_chars: int = 4000) -> None:
        """注册工具。同名重复注册直接报错（防静默覆盖越权）。"""
        if name in self._tools:
            raise ValueError(f"工具 '{name}' 已注册（防静默覆盖）")
        self._tools[name] = Tool(name, desc, fn, arg_schema or {},
                                 max_result_chars)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def has(self, name: str) -> bool:
        return name in self._tools

    def describe(self) -> str:
        """给 planner 的工具清单文本。"""
        lines = []
        for t in sorted(self._tools.values(), key=lambda x: x.name):
            args = ", ".join(
                f"{k}:{v.get('type', 'any')}{'?' if not v.get('required', True) else ''}"
                for k, v in t.arg_schema.items())
            lines.append(f"- {t.name}({args}): {t.desc}")
        return "\n".join(lines)

    def validate_args(self, name: str, args: dict) -> str | None:
        """参数校验：返回错误文案，None 表示合法。

        修复 A4：schema 未声明的额外参数显式拒绝（防 LLM 塞越权参数，
        不依赖执行期 TypeError 兜底）。
        """
        if name not in self._tools:
            return f"未知工具 '{name}'（可用: {self.names()}）"
        schema = self._tools[name].arg_schema
        if not isinstance(args, dict):
            return f"参数须为 dict，得到 {type(args).__name__}"
        for k in args:
            if k not in schema:
                return f"未知参数 '{k}'（schema 仅允许: {sorted(schema)}）"
        for k, spec in schema.items():
            if k not in args:
                if spec.get("required", True):
                    return f"缺参数 '{k}'"
                continue
            v = args[k]
            tname = spec.get("type")
            if tname and tname in _TYPES:
                if tname == "float" and isinstance(v, int) and not isinstance(v, bool):
                    v = float(v)  # int 可升 float（宽松）
                if tname == "int" and isinstance(v, bool):
                    return f"参数 '{k}' 须 int，得到 bool"
                if not isinstance(v, _TYPES[tname]):
                    return (f"参数 '{k}' 须 {tname}，"
                            f"得到 {type(v).__name__}")
        return None

    def call(self, name: str, args: dict) -> ToolCallResult:
        """调用工具（先校验后执行，异常转结构化错误）。"""
        err = self.validate_args(name, args)
        if err is not None:
            return ToolCallResult(ok=False, error=f"[校验失败] {err}")
        try:
            val = self._tools[name].fn(**args)
        except Exception as exc:
            return ToolCallResult(ok=False,
                                  error=f"[执行失败] {type(exc).__name__}: {exc}")
        truncated = False
        if isinstance(val, str) and len(val) > self._tools[name].max_result_chars:
            limit = self._tools[name].max_result_chars
            val = val[:limit] + f"\n[...截断 {len(val) - limit} 字符]"
            truncated = True
        return ToolCallResult(ok=True, value=val, truncated=truncated)
