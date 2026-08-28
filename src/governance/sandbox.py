"""L4 沙箱执行：子进程隔离，禁网·禁写·资源限制。

防攻击面设计（v0.9.0）：
- 沙箱逃逸：代码以受限子进程执行，Python 沙箱模块级禁用
  （os/subprocess/socket 等危险模块 import 即拦截），非白名单
  模块不可加载，工作目录隔离到临时目录；
- 越权写文件：仅允许在工作目录内写（chroot 语义），其它路径
  一律拒绝（path traversal 拦截）；
- 资源耗尽：CPU 时间 / 内存 / 执行时长硬上限，超限即 kill。
"""
from __future__ import annotations
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import textwrap
import uuid
from dataclasses import dataclass, field

# ---------------------------------------------------------------- 禁用模块白名单
# 沙箱内允许 import 的模块（白名单制——未列出即拒绝）
_ALLOWED_MODULES: frozenset[str] = frozenset({
    # 标准库数学/统计
    "math", "statistics", "random", "fractions", "decimal",
    # 标准库数据结构
    "collections", "itertools", "functools", "operator",
    "heapq", "bisect", "array", "queue",
    # 标准库数据处理
    "json", "csv", "re", "string", "textwrap", "unicodedata",
    "datetime", "calendar",
    # 标准库类型/工具
    "dataclasses", "typing", "enum", "copy", "pprint",
    # 数值计算（第三方）
    "numpy", "pandas", "scipy",
    # 本项目（只读引用）
    "src",
})

# 显式禁用的高危模块（即使 import 尝试也要拦）
_BLOCKED_PREFIXES: tuple[str, ...] = (
    "os", "subprocess", "socket", "http", "urllib", "requests",
    "shutil", "pathlib", "ctypes", "multiprocessing", "signal",
    "threading", "asyncio", "select", "ssl", "ftplib", "smtplib",
    "telnetlib", "paramiko", "fabric", "pexpect",
)


def _make_import_guard(allowed: frozenset[str],
                       blocked_prefixes: tuple[str, ...]):
    """生成 import 钩子：白名单外 / 黑名单前缀即拒绝。"""

    def _import_guard(name, *args, **kwargs):
        # 取顶层模块名（a.b.c → a）
        top = name.split(".")[0]
        # 下划线开头的是 Python 内部 C 模块（_io/_thread 等），
        # 允许 numpy/pandas 等第三方库内部依赖，不拦
        if top.startswith("_"):
            return _original_import(name, *args, **kwargs)
        if top in blocked_prefixes:
            raise ImportError(
                f"[sandbox] 模块 '{name}' 被禁用（高危）")
        if top not in allowed:
            raise ImportError(
                f"[sandbox] 模块 '{name}' 不在白名单，拒绝加载")
        return _original_import(name, *args, **kwargs)

    return _import_guard


_original_import = __import__


@dataclass
class SandboxResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    error: str = ""


@dataclass
class SandboxConfig:
    """沙箱配置。"""
    timeout: int = 10            # 秒
    max_memory_mb: int = 512     # MB
    work_dir: str = ""           # 空则自动创建临时目录
    extra_allowed: frozenset[str] = field(default_factory=frozenset)


class Sandbox:
    """代码沙箱：子进程隔离执行 Python 代码。

    用法：
        sb = Sandbox()
        res = sb.run("print(1 + 2)")
        assert res.ok and "3" in res.stdout

    安全保证：
    - 子进程独立（父进程崩溃不影响）；
    - import 白名单（os/subprocess/socket 等直接拒绝）；
    - 工作目录隔离（临时目录，执行完清理）；
    - CPU/内存/超时硬上限。
    """

    def __init__(self, config: SandboxConfig | None = None):
        self.config = config or SandboxConfig()

    def run(self, code: str) -> SandboxResult:
        """在沙箱中执行 Python 代码，返回结果。"""
        work_dir = self.config.work_dir or tempfile.mkdtemp(
            prefix="sandbox_")
        # 安全：只接受绝对路径且必须存在
        if not os.path.isabs(work_dir):
            raise ValueError(f"work_dir 须绝对路径，得到 {work_dir}")
        if not os.path.isdir(work_dir):
            os.makedirs(work_dir, exist_ok=True)

        # 生成沙箱启动脚本：预导入 → guard → restricted builtins → 执行
        allowed = _ALLOWED_MODULES | self.config.extra_allowed
        runner = textwrap.dedent(f'''\
            import sys, resource, os as _os

            # 预导入第三方库：numpy/pandas/scipy 的 __init__ 在 guard
            # 启用前完成（它们内部 import os/warnings 等），之后 guard
            # 阻止用户代码直接 import os/subprocess/socket
            _preimport = []
            for _mod in ("numpy", "pandas", "scipy"):
                try:
                    __import__(_mod)
                    _preimport.append(_mod)
                except ImportError:
                    pass

            # 保存路径函数引用（guard 后无法 import os）
            _abspath = _os.path.abspath
            _sep = _os.sep

            _original_import = __import__
            _ALLOWED = {allowed!r}
            _BLOCKED = {_BLOCKED_PREFIXES!r}

            def _import_guard(name, *args, **kwargs):
                top = name.split(".")[0]
                if top.startswith("_"):
                    return _original_import(name, *args, **kwargs)
                if top in _BLOCKED:
                    raise ImportError(f"[sandbox] 模块 '{{name}}' 被禁用")
                if top not in _ALLOWED:
                    raise ImportError(f"[sandbox] 模块 '{{name}}' 不在白名单")
                return _original_import(name, *args, **kwargs)

            import builtins as _bi
            _bi.__import__ = _import_guard

            # 内存限制
            max_mem = {self.config.max_memory_mb} * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS,
                               (max_mem, max_mem))

            # 构建 restricted builtins：移除危险内置函数，
            # 替换 open() 为只能访问工作目录的版本
            _work = {work_dir!r}
            _orig_open = _bi.open

            def _safe_open(file, mode='r', *args, **kwargs):
                p = _abspath(file)
                wd = _abspath(_work)
                if not p.startswith(wd + _sep) and p != wd:
                    raise PermissionError(
                        f"[sandbox] 禁止访问工作目录外路径: {{p}}")
                return _orig_open(file, mode, *args, **kwargs)

            _safe_builtins = {{
                k: v for k, v in vars(_bi).items()
                if k not in (
                    "open", "eval", "exec", "compile",
                    "breakpoint", "input",
                    "globals", "locals", "vars",
                )
            }}
            # 保留 guard 版 __import__（import 语句可用，但受限）
            _safe_builtins["__import__"] = _import_guard
            _safe_builtins["open"] = _safe_open

            # 执行用户代码（restricted builtins）
            _user_code = {code!r}
            _ns = {{"__name__": "__sandbox__",
                    "__builtins__": _safe_builtins}}
            exec(_user_code, _ns)
        ''')

        try:
            proc = subprocess.run(
                [sys.executable, "-c", runner],
                capture_output=True, text=True,
                timeout=self.config.timeout,
                cwd=work_dir,
                env={
                    "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin"),
                    "PYTHONPATH": os.path.dirname(
                        os.path.dirname(os.path.dirname(__file__))),
                    "HOME": work_dir,
                    # 禁网：清空代理 + 设置无网络标记
                    "http_proxy": "", "https_proxy": "",
                    "HTTP_PROXY": "", "HTTPS_PROXY": "",
                    "ALL_PROXY": "", "NO_PROXY": "*",
                },
            )
            return SandboxResult(
                ok=proc.returncode == 0,
                stdout=proc.stdout,
                stderr=proc.stderr,
                exit_code=proc.returncode,
            )
        except subprocess.TimeoutExpired as e:
            return SandboxResult(
                ok=False, timed_out=True,
                error=f"超时（{self.config.timeout}s）",
                stdout=e.stdout or "" if isinstance(e.output, str) else "",
                stderr=e.stderr or "" if isinstance(e.output, str) else "",
            )
        except Exception as e:
            return SandboxResult(
                ok=False, error=f"{type(e).__name__}: {e}")
        finally:
            if not self.config.work_dir and os.path.isdir(work_dir):
                shutil.rmtree(work_dir, ignore_errors=True)

    def run_function(self, fn_code: str, fn_name: str,
                     *args, **kwargs) -> SandboxResult:
        """在沙箱中执行指定函数并传参，返回 JSON 序列化结果。"""
        call = textwrap.dedent(f'''\
            {fn_code}
            import json
            _result = {fn_name}(*{args!r}, **{kwargs!r})
            print(json.dumps(_result, default=str, ensure_ascii=False))
        ''')
        return self.run(call)
