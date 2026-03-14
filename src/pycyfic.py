from __future__ import annotations

import functools
import hashlib
import importlib.util
import inspect
import sys
import textwrap
import types
from collections import OrderedDict
from importlib.machinery import ExtensionFileLoader
from pathlib import Path
from threading import RLock

from pyximport.pyxbuild import pyx_to_dll
from setuptools import Extension

from compiler import transpile, ALIAS


BUILD_DIR_NAME = ".pycyfic_build"

_FILE_STATES: dict[str, "_FileBuildState"] = {}
_STATE_LOCK = RLock()


class _RegisteredFunction:
    __slots__ = ("name", "original_fn", "py_source", "pyx_source")

    def __init__(self, name: str, original_fn, py_source: str, pyx_source: str):
        self.name = name
        self.original_fn = original_fn
        self.py_source = py_source
        self.pyx_source = pyx_source


class _FileBuildState:
    __slots__ = (
        "source_path",
        "pyx_path",
        "build_dir",
        "functions",
        "version",
        "compiled_version",
        "compiled_module",
        "compiled_module_name",
    )

    def __init__(self, source_path: str):
        path = Path(source_path).resolve()
        self.source_path = path
        self.pyx_path = path.with_suffix(".pyx")
        self.build_dir = path.parent / BUILD_DIR_NAME
        self.functions: OrderedDict[str, _RegisteredFunction] = OrderedDict()
        self.version = 0
        self.compiled_version = -1
        self.compiled_module = None
        self.compiled_module_name = None

    def register(self, fn, py_source: str) -> None:
        pyx_source = transpile(py_source, alias=ALIAS, force_cpdef=True)

        old = self.functions.get(fn.__name__)
        if old is not None and old.pyx_source == pyx_source:
            return

        self.functions[fn.__name__] = _RegisteredFunction(
            name=fn.__name__,
            original_fn=fn,
            py_source=py_source,
            pyx_source=pyx_source,
        )

        self.version += 1
        self.compiled_version = -1
        self.compiled_module = None
        self.compiled_module_name = None

    def render_pyx(self) -> str:
        blocks: list[str] = []

        for item in self.functions.values():
            block = item.pyx_source.rstrip()
            if block:
                blocks.append(block)

        if not blocks:
            return ""

        return "\n\n".join(blocks) + "\n"


def _get_state(full_path: str) -> _FileBuildState:
    key = str(Path(full_path).resolve())

    with _STATE_LOCK:
        state = _FILE_STATES.get(key)
        if state is None:
            state = _FileBuildState(key)
            _FILE_STATES[key] = state
        return state


def _strip_leading_decorators(src: str) -> str:
    lines = src.splitlines(True)

    while lines and lines[0].lstrip().startswith("@"):
        lines.pop(0)

    return "".join(lines)


def _read_function_source(fn) -> str:
    try:
        src = inspect.getsource(fn)
    except OSError as e:
        raise RuntimeError(
            f"optimize(): cannot read source for {fn.__name__}. "
            f"Make sure it is defined in a real .py file."
        ) from e

    src = textwrap.dedent(src)
    src = _strip_leading_decorators(src)
    return src


def _module_digest(pyx_text: str) -> str:
    return hashlib.sha1(pyx_text.encode("utf-8")).hexdigest()[:12]


def _private_module_name(state: _FileBuildState, pyx_text: str) -> str:
    digest = _module_digest(pyx_text)
    stem = state.source_path.stem
    return f"_pycyfic_{stem}_{digest}"


def _write_pyx_file(state: _FileBuildState, pyx_text: str) -> None:
    state.pyx_path.write_text(pyx_text, encoding="utf-8")


def _load_extension_module(module_name: str, shared_object_path: str):
    loader = ExtensionFileLoader(module_name, shared_object_path)
    spec = importlib.util.spec_from_file_location(
        module_name,
        shared_object_path,
        loader=loader,
    )

    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create import spec for compiled module {module_name!r}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _compile_full_file_module(state: _FileBuildState):
    pyx_text = state.render_pyx()
    if not pyx_text.strip():
        raise RuntimeError(f"No optimized functions registered for {state.source_path.name}")

    state.build_dir.mkdir(parents=True, exist_ok=True)
    _write_pyx_file(state, pyx_text)

    module_name = _private_module_name(state, pyx_text)

    ext = Extension(
        name=module_name,
        sources=[str(state.pyx_path)],
    )

    shared_object_path = pyx_to_dll(
        str(state.pyx_path),
        ext=ext,
        pyxbuild_dir=str(state.build_dir),
        build_in_temp=False,
        inplace=False,
        reload_support=True,
    )

    module = _load_extension_module(module_name, shared_object_path)

    state.compiled_module = module
    state.compiled_module_name = module_name
    state.compiled_version = state.version
    return module


def _inject_globals(module, fn, optimized_names: set[str]) -> None:
    skip = {
        "__name__",
        "__file__",
        "__package__",
        "__loader__",
        "__spec__",
        "__cached__",
    } | optimized_names

    module.__dict__["__builtins__"] = fn.__globals__.get("__builtins__", __builtins__)

    for key, value in fn.__globals__.items():
        if key in skip:
            continue
        module.__dict__[key] = value


def _inject_closure(module, fn) -> None:
    closure = getattr(fn, "__closure__", None)
    freevars = getattr(fn.__code__, "co_freevars", ())

    if not closure:
        return

    for var_name, cell in zip(freevars, closure):
        try:
            module.__dict__[var_name] = cell.cell_contents
        except ValueError:
            pass


def _get_compiled_function(state: _FileBuildState, fn):
    with _STATE_LOCK:
        if state.compiled_module is None or state.compiled_version != state.version:
            module = _compile_full_file_module(state)
        else:
            module = state.compiled_module

    optimized_names = set(state.functions.keys())
    _inject_globals(module, fn, optimized_names)
    _inject_closure(module, fn)

    compiled_fn = getattr(module, fn.__name__)

    for attr in ("__doc__", "__annotations__"):
        try:
            setattr(compiled_fn, attr, getattr(fn, attr))
        except Exception:
            pass

    return compiled_fn


def optimize(fn):
    full_path = inspect.getfile(fn)
    state = _get_state(full_path)

    src = _read_function_source(fn)
    state.register(fn, src)

    compiled_cache = None
    compiled_cache_version = -1

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        nonlocal compiled_cache, compiled_cache_version

        if compiled_cache is None or compiled_cache_version != state.version:
            compiled_cache = _get_compiled_function(state, fn)
            compiled_cache_version = state.version

        return compiled_cache(*args, **kwargs)

    wrapper.__pycyfic_original__ = fn
    wrapper.__pycyfic_source_path__ = str(state.source_path)
    wrapper.__pycyfic_pyx_path__ = str(state.pyx_path)

    return wrapper


def build_bytecode(fn) -> bytes:
    if isinstance(fn, types.CodeType):
        code = fn
    else:
        code = getattr(fn, "__code__", None)

    if not isinstance(code, types.CodeType):
        raise TypeError(
            "build_bytecode(fn): expected a Python function (with __code__) or a code object"
        )

    return code.co_code
