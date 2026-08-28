"""YAML 配置加载。"""
from __future__ import annotations
from pathlib import Path
import yaml


def load_config(path: str | Path = "config/data.yml") -> dict:
    """加载 YAML 配置，返回字典。文件不存在返回空字典。"""
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
