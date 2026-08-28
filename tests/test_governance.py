"""v0.9.0 L4 治理层测试：沙箱 / 审计 / 人审接口。"""
import pytest

from src.governance import (
    Sandbox, SandboxConfig, SandboxResult,
    AuditTrail, AuditStatus,
    HumanGate, InvestmentGoal, Decision,
)


# ================================================================ 沙箱
class TestSandbox:
    def test_正常代码执行(self):
        sb = Sandbox(SandboxConfig(timeout=5))
        res = sb.run("print(1 + 2)")
        assert res.ok
        assert "3" in res.stdout

    def test_os模块被禁(self):
        sb = Sandbox(SandboxConfig(timeout=5))
        res = sb.run("import os; os.listdir('/')")
        assert not res.ok
        assert "禁" in res.stderr or "blocked" in res.stderr.lower()

    def test_subprocess被禁(self):
        sb = Sandbox(SandboxConfig(timeout=5))
        res = sb.run("import subprocess; subprocess.run(['ls'])")
        assert not res.ok

    def test_socket被禁(self):
        sb = Sandbox(SandboxConfig(timeout=5))
        res = sb.run("import socket; socket.socket()")
        assert not res.ok

    def test_numpy可用(self):
        sb = Sandbox(SandboxConfig(timeout=10))
        res = sb.run("import numpy as np; print(np.array([1,2,3]).sum())")
        assert res.ok
        assert "6" in res.stdout

    def test_超时终止(self):
        sb = Sandbox(SandboxConfig(timeout=2))
        res = sb.run("x = 0\nwhile True:\n    x += 1")
        assert not res.ok
        assert res.timed_out

    def test_工作目录隔离与清理(self):
        import tempfile, os
        d = tempfile.mkdtemp()
        sb = Sandbox(SandboxConfig(timeout=5, work_dir=d))
        # 写文件到工作目录
        res = sb.run("open('test.txt','w').write('hello')")
        assert res.ok
        assert os.path.exists(os.path.join(d, "test.txt"))
        # 清理临时目录
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    def test_非白名单模块拒绝(self):
        sb = Sandbox(SandboxConfig(timeout=5))
        # requests 不在白名单
        res = sb.run("import requests")
        assert not res.ok


# ================================================================ 审计
class TestAuditTrail:
    def test_提交待审需要证据(self, tmp_path):
        trail = AuditTrail(str(tmp_path / "audit.jsonl"))
        # 无证据提交 → 拒绝
        with pytest.raises(ValueError, match="evidence"):
            trail.submit("factor", "alpha001", "rank(close)")

    def test_提交与审核通过(self, tmp_path):
        trail = AuditTrail(str(tmp_path / "audit.jsonl"))
        assert trail.submit("factor", "alpha001", "rank(close)",
                            evidence={"sharpe": 1.5, "oos_decay": 0.7}) is True
        assert trail.approve("alpha001", "rank(close)", "reviewer_A") is True
        assert trail.is_approved("alpha001", "rank(close)") is True

    def test_未审核不可入库(self, tmp_path):
        trail = AuditTrail(str(tmp_path / "audit.jsonl"))
        trail.submit("strategy", "ma_cross", "MA5>MA20",
                     evidence={"sharpe": 1.0})
        # 只提交未审核 → is_approved=False
        assert trail.is_approved("ma_cross", "MA5>MA20") is False

    def test_驳回记录原因(self, tmp_path):
        trail = AuditTrail(str(tmp_path / "audit.jsonl"))
        trail.submit("factor", "alpha002", "rank(open)",
                     evidence={"sharpe": 0.1})
        assert trail.reject("alpha002", "rank(open)", "reviewer_B",
                            "夏普过低") is True
        assert trail.is_approved("alpha002", "rank(open)") is False

    def test_重复审核拒绝(self, tmp_path):
        trail = AuditTrail(str(tmp_path / "audit.jsonl"))
        trail.submit("factor", "alpha003", "delta(close)",
                     evidence={"sharpe": 2.0})
        trail.approve("alpha003", "delta(close)", "reviewer_A")
        # 重复审核 → False
        assert trail.approve("alpha003", "delta(close)",
                             "reviewer_A") is False

    def test_撤回审核(self, tmp_path):
        trail = AuditTrail(str(tmp_path / "audit.jsonl"))
        trail.submit("factor", "alpha004", "ts_rank(close)",
                     evidence={"sharpe": 1.8})
        trail.approve("alpha004", "ts_rank(close)", "reviewer_A")
        assert trail.is_approved("alpha004", "ts_rank(close)") is True
        # 撤回
        trail.revoke("alpha004", "ts_rank(close)", "reviewer_A",
                     "上线后发现前视")
        assert trail.is_approved("alpha004", "ts_rank(close)") is False

    def test_非法类型拒绝(self, tmp_path):
        trail = AuditTrail(str(tmp_path / "audit.jsonl"))
        with pytest.raises(ValueError, match="artifact_type"):
            trail.submit("invalid_type", "x", "y", evidence={"k": 1})

    def test_重复提交去重(self, tmp_path):
        trail = AuditTrail(str(tmp_path / "audit.jsonl"))
        assert trail.submit("factor", "alpha005", "rank(close)",
                            evidence={"sharpe": 1.0}) is True
        # 同内容重复提交 → 拒绝
        assert trail.submit("factor", "alpha005", "rank(close)",
                            evidence={"sharpe": 1.0}) is False


# ================================================================ 人审接口
class TestHumanGate:
    def test_默认deny(self, tmp_path):
        gate = HumanGate(str(tmp_path / "gate.jsonl"))
        req_id = gate.request_review("register_strategy", "ma_cross")
        # 未决策 → 不通过
        assert gate.is_approved(req_id) is False
        assert gate.get_decision(req_id) == Decision.DEFER

    def test_审核通过(self, tmp_path):
        gate = HumanGate(str(tmp_path / "gate.jsonl"))
        req_id = gate.request_review("register_factor", "alpha001")
        assert gate.decide(req_id, Decision.APPROVE, "user_A") is True
        assert gate.is_approved(req_id) is True
        assert gate.get_decision(req_id) == Decision.APPROVE

    def test_审核驳回(self, tmp_path):
        gate = HumanGate(str(tmp_path / "gate.jsonl"))
        req_id = gate.request_review("live_trade", "strategy_X")
        gate.decide(req_id, Decision.REJECT, "user_B", "风险过高")
        assert gate.is_approved(req_id) is False
        assert gate.get_decision(req_id) == Decision.REJECT

    def test_重复决策拒绝(self, tmp_path):
        gate = HumanGate(str(tmp_path / "gate.jsonl"))
        req_id = gate.request_review("register", "alpha_X")
        gate.decide(req_id, Decision.APPROVE, "user_A")
        # 重复决策 → False
        assert gate.decide(req_id, Decision.REJECT, "user_B") is False

    def test_不存在的请求(self, tmp_path):
        gate = HumanGate(str(tmp_path / "gate.jsonl"))
        assert gate.decide("nonexistent", Decision.APPROVE, "u") is False
        assert gate.is_approved("nonexistent") is False


# ================================================================ 目标校验
class TestInvestmentGoal:
    def test_合法目标(self):
        goal = InvestmentGoal(
            target_return=0.2, max_drawdown=-0.15,
            stock_pool=["600519", "000858"])
        assert goal.validate() == []

    def test_收益越界(self):
        goal = InvestmentGoal(target_return=100)
        assert any("target_return" in e for e in goal.validate())

    def test_回撤正值非法(self):
        goal = InvestmentGoal(max_drawdown=0.5)
        assert any("max_drawdown" in e for e in goal.validate())

    def test_空目标合法(self):
        goal = InvestmentGoal()
        assert goal.validate() == []

    def test_股票池超上限(self):
        goal = InvestmentGoal(stock_pool=[str(i) for i in range(501)])
        assert any("超上限" in e for e in goal.validate())


# ================================================================ Attacker 回归
class TestAttackerRegression:
    """v0.9.0 Attacker 攻击点回归。"""

    def test_open不可逃逸到工作目录外(self, tmp_path):
        sb = Sandbox(SandboxConfig(timeout=5, work_dir=str(tmp_path)))
        res = sb.run("open('/etc/hostname').read()")
        assert not res.ok
        assert "禁止" in res.stderr or "Permission" in res.stderr

    def test_open可在工作目录内读写(self, tmp_path):
        sb = Sandbox(SandboxConfig(timeout=5, work_dir=str(tmp_path)))
        res = sb.run("open('test.txt','w').write('ok')")
        assert res.ok

    def test_eval被移除(self):
        sb = Sandbox(SandboxConfig(timeout=5))
        res = sb.run("eval('1+1')")
        assert not res.ok

    def test_exec被移除(self):
        sb = Sandbox(SandboxConfig(timeout=5))
        res = sb.run("exec('print(1)')")
        assert not res.ok

    def test_路径穿越拦截(self, tmp_path):
        import os
        sb = Sandbox(SandboxConfig(timeout=5, work_dir=str(tmp_path)))
        # ../etc/hostname 路径穿越
        res = sb.run("open('../etc/hostname').read()")
        assert not res.ok

    def test_HumanGate可撤回决策(self, tmp_path):
        gate = HumanGate(str(tmp_path / "gate.jsonl"))
        req_id = gate.request_review("register", "alpha_X")
        gate.decide(req_id, Decision.APPROVE, "user_A")
        assert gate.is_approved(req_id) is True
        # 撤回
        assert gate.revoke(req_id, "user_A", "发现问题") is True
        assert gate.is_approved(req_id) is False
        assert gate.get_decision(req_id) == Decision.DEFER

    def test_HumanGate重复撤回拒绝(self, tmp_path):
        gate = HumanGate(str(tmp_path / "gate.jsonl"))
        req_id = gate.request_review("register", "alpha_Y")
        gate.decide(req_id, Decision.APPROVE, "user_A")
        gate.revoke(req_id, "user_A", "reason1")
        # 重复撤回 → False
        assert gate.revoke(req_id, "user_A", "reason2") is False

    def test_HumanGate未决策不可撤回(self, tmp_path):
        gate = HumanGate(str(tmp_path / "gate.jsonl"))
        req_id = gate.request_review("register", "alpha_Z")
        # 未做决策 → 无可撤回
        assert gate.revoke(req_id, "user_A", "reason") is False
