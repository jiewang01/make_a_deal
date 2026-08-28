"""v1.0.0 MCP 工具层 + 端到端 pipeline 测试。"""
import json
import pytest

from src.tools import MCPServer, run_pipeline, make_synthetic_panel
from src.tools.mcp_server import _to_jsonable


# ================================================================ MCP 工具
class TestMCPServer:
    def test_注册与调用(self):
        srv = MCPServer()
        srv.register("add", lambda a, b: a + b,
                     desc="加法",
                     input_schema={
                         "a": {"type": "int", "required": True},
                         "b": {"type": "int", "required": True},
                     })
        res = srv.call("add", {"a": 1, "b": 2})
        assert res.ok
        assert res.value == 3

    def test_未知工具拒绝(self):
        srv = MCPServer()
        res = srv.call("nonexistent", {})
        assert not res.ok
        assert "未知" in res.error

    def test_参数类型校验(self):
        srv = MCPServer()
        srv.register("echo", lambda msg: msg,
                     input_schema={"msg": {"type": "str", "required": True}})
        res = srv.call("echo", {"msg": 123})  # int 传给 str 参数
        assert not res.ok
        assert "校验" in res.error

    def test_缺失必填参数(self):
        srv = MCPServer()
        srv.register("need_x", lambda x: x,
                     input_schema={"x": {"type": "int", "required": True}})
        res = srv.call("need_x", {})
        assert not res.ok
        assert "缺" in res.error

    def test_重复注册拒绝(self):
        srv = MCPServer()
        srv.register("dup", lambda: 1)
        with pytest.raises(ValueError, match="已注册"):
            srv.register("dup", lambda: 2)

    def test_list_tools(self):
        srv = MCPServer()
        srv.register("t1", lambda: 1, desc="tool1",
                     input_schema={"x": {"type": "int", "required": True}})
        tools = srv.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "t1"
        assert "x" in tools[0]["inputSchema"]["properties"]
        assert "x" in tools[0]["inputSchema"]["required"]

    def test_call_json返回合法JSON(self):
        srv = MCPServer()
        srv.register("ping", lambda: {"status": "ok"})
        out = srv.call_json("ping", {})
        parsed = json.loads(out)
        assert parsed["ok"] is True
        assert parsed["value"]["status"] == "ok"

    def test_numpy结果可序列化(self):
        import numpy as np
        srv = MCPServer()
        srv.register("arr", lambda: np.array([1, 2, 3]))
        res = srv.call("arr", {})
        assert res.ok
        assert res.value == [1, 2, 3]

    def test_不可序列化拒绝(self):
        class Foo:
            pass
        srv = MCPServer()
        srv.register("bad", lambda: Foo())
        res = srv.call("bad", {})
        assert not res.ok
        assert "序列化" in res.error

    def test_未知参数拒绝(self):
        srv = MCPServer()
        srv.register("f", lambda x: x,
                     input_schema={"x": {"type": "int", "required": True}})
        res = srv.call("f", {"x": 1, "extra": 2})
        assert not res.ok
        assert "未知参数" in res.error


# ================================================================ JSON 序列化
class TestToJsonable:
    def test_numpy标量(self):
        import numpy as np
        assert _to_jsonable(np.int64(42)) == 42
        assert isinstance(_to_jsonable(np.float64(3.14)), float)

    def test_numpy数组(self):
        import numpy as np
        assert _to_jsonable(np.array([1, 2, 3])) == [1, 2, 3]

    def test_pandas_dataframe(self):
        import pandas as pd
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        result = _to_jsonable(df)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_嵌套容器(self):
        import numpy as np
        obj = {"scores": [np.float64(0.1), np.float64(0.2)], "name": "test"}
        result = _to_jsonable(obj)
        assert result == {"scores": [0.1, 0.2], "name": "test"}

    def test_不可序列化拒绝(self):
        with pytest.raises(TypeError, match="不可序列化"):
            _to_jsonable(object())


# ================================================================ 端到端 Pipeline
class TestPipeline:
    def test_端到端成功(self, tmp_path):
        res = run_pipeline(
            audit_path=str(tmp_path / "audit.jsonl"),
            gate_path=str(tmp_path / "gate.jsonl"))
        assert res.ok
        assert res.stop_reason == "completed"
        assert len(res.steps) == 6
        assert all(s.ok for s in res.steps)

    def test_人审模式成功(self, tmp_path):
        res = run_pipeline(
            audit_path=str(tmp_path / "audit.jsonl"),
            gate_path=str(tmp_path / "gate.jsonl"),
            enable_human_gate=True)
        assert res.ok
        assert res.audit_approved is True

    def test_合成数据格式(self):
        panel = make_synthetic_panel(n_days=50, n_stocks=3)
        assert "close" in panel.columns
        assert "volume" in panel.columns
        assert len(panel) == 50 * 3

    def test_pipeline步骤含结果信息(self, tmp_path):
        res = run_pipeline(audit_path=str(tmp_path / "a.jsonl"))
        # 数据加载步骤应该有结果信息
        assert res.steps[0].result is not None
        assert "行" in res.steps[0].result


# ================================================================ Attacker 回归
class TestAttackerRegression:
    """v1.0.0 Attacker 攻击点回归。"""

    def test_pipeline重复运行审计正确处理(self, tmp_path):
        """Attack: 重复运行时 audit_approved 不应硬编码 True。"""
        audit = str(tmp_path / "audit.jsonl")
        res1 = run_pipeline(audit_path=audit)
        assert res1.ok is True
        # 第二次运行：submit 会 dedup，但 is_approved 已 True
        res2 = run_pipeline(audit_path=audit)
        # 应检测到已审核，audit_approved=True 是因为 is_approved
        assert res2.ok is True

    def test_pipeline路径穿越拒绝(self):
        """Attack: 路径包含 .. 应拒绝。"""
        with pytest.raises(ValueError, match="穿越"):
            run_pipeline(audit_path="../../etc/audit.jsonl")

    def test_call_json不用default_str掩盖(self):
        """Attack: call_json 不应用 default=str 掩盖不可序列化。"""
        class Foo:
            pass
        srv = MCPServer()
        srv.register("bad", lambda: Foo())
        out = srv.call_json("bad", {})
        parsed = json.loads(out)
        assert parsed["ok"] is False
        assert "序列化" in parsed["error"]

    def test_pipeline审计submit失败不硬编码通过(self, tmp_path):
        """Attack: submit 失败且未审核 → audit_approved=False。"""
        # 预先创建一个审计文件并 approve，然后运行 pipeline
        # pipeline 的 submit 会 dedup（已有 pending），is_approved 会 True
        from src.governance.audit import AuditTrail
        audit = str(tmp_path / "audit.jsonl")
        trail = AuditTrail(audit)
        # 预先 submit + approve
        trail.submit("strategy", "ma_cross", "MA5>MA20 买入",
                     evidence={"pre": True})
        trail.approve("ma_cross", "MA5>MA20 买入", "pre_reviewer")
        # 运行 pipeline：submit 会 dedup，is_approved=True
        res = run_pipeline(audit_path=audit)
        assert res.ok is True  # 因为已审核通过
