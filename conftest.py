"""pytest 根配置：让 `from src.xxx` 可 import + 注册 markers。"""
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config):
    config.addinivalue_line("markers", "online: 需网络的在线集成测试")
