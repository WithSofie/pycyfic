from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import dataclass
from typing import List, Mapping, Optional, Tuple


ALIAS = "cy"

DEFAULT_TYPE_MAP: dict[str, str] = {
    "longlong": "long long",
    "ulonglong": "unsigned long long",
    "longdouble": "long double",
    "shortint": "short int",
    "ushortint": "unsigned short int",
    "uint": "unsigned int",
    "ulong": "unsigned long",
    "ssize_t": "Py_ssize_t",
}


@dataclass(frozen=True)
class _Edit:
    start: int
    end: int
    replacement: str


def _ensure_text_source(source) -> str:
    if isinstance(source, str):
        return source

    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(source).decode("utf-8")

    if isinstance(source, (list, tuple)):
        # inspect.getsourcelines() -> (lines, startline)
        if len(source) == 2 and isinstance(source[0], (list, tuple)):
            return _ensure_text_source(source[0])

        if all(isinstance(x, (str, bytes, bytearray, memoryview)) for x in source):
            parts: List[str] = []
            for x in source:
                if isinstance(x, str):
                    parts.append(x)
                else:
                    parts.append(bytes(x).decode("utf-8"))
            return "".join(parts)

    read = getattr(source, "read", None)
    if callable(read):
        return _ensure_text_source(read())

    raise TypeError(
        f"transpile/cythonize_annotations expected source as str/bytes/lines, got {type(source).__name__}"
    )


def _line_starts(src: str) -> List[int]:
    starts = [0]
    for i, ch in enumerate(src):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _abs_index(starts: List[int], lineno_1: int, col: int) -> int:
    return starts[lineno_1 - 1] + col


def _unparse(node: ast.AST) -> str:
    if hasattr(ast, "unparse"):
        return ast.unparse(node)  # type: ignore[attr-defined]
    raise RuntimeError("Python 3.9+ required (ast.unparse not available).")


def _source_segment(src: str, node: ast.AST) -> Optional[str]:
    try:
        return ast.get_source_segment(src, node)
    except Exception:
        return None


def _is_decorator_blocking(deco: ast.expr) -> bool:
    if isinstance(deco, ast.Name) and deco.id in {"property", "staticmethod", "classmethod"}:
        return True
    if isinstance(deco, ast.Attribute) and deco.attr in {"setter", "deleter"}:
        return True
    return False


def _match_cython_type_expr(
    src: str,
    ann: ast.AST,
    alias: str,
    type_map: Mapping[str, str],
) -> Optional[str]:
    seg = _source_segment(src, ann)
    if seg is None:
        seg = _unparse(ann)

    seg = seg.strip()

    if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
        seg = ann.value.strip()

    prefixes = ("cython.", f"{alias}.")
    prefix = None
    for p in prefixes:
        if seg.startswith(p):
            prefix = p
            break

    if prefix is None:
        return None

    rest = seg[len(prefix):]
    base = rest
    tail = ""

    for i, ch in enumerate(rest):
        if ch in "[(":
            base = rest[:i]
            tail = rest[i:]
            break

    base = base.strip()
    if not base:
        return None

    mapped = type_map.get(base, base)
    return f"{mapped}{tail}"


def _find_def_header_span(src: str, func: ast.FunctionDef) -> Optional[Tuple[int, int]]:
    starts = _line_starts(src)
    target_line = func.lineno
    target_col = func.col_offset

    tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))

    i_def = None
    for i, tok in enumerate(tokens):
        if tok.type == tokenize.NAME and tok.string == "def" and tok.start == (target_line, target_col):
            i_def = i
            break

    if i_def is None:
        return None

    paren = 0
    for j in range(i_def, len(tokens)):
        tok = tokens[j]
        s = tok.string

        if s in "([{":
            paren += 1
        elif s in ")]}":
            paren = max(0, paren - 1)
        elif s == ":" and paren == 0:
            start_abs = _abs_index(starts, *tokens[i_def].start)
            end_abs = _abs_index(starts, *tok.end)
            return (start_abs, end_abs)

    return None


def _build_cpdef_header(
    src: str,
    func: ast.FunctionDef,
    alias: str,
    type_map: Mapping[str, str],
    force_cpdef: bool,
) -> Optional[str]:
    if getattr(func.args, "posonlyargs", []):
        return None

    for d in func.decorator_list:
        if _is_decorator_blocking(d):
            return None

    has_any_cython_sig_type = False

    def fmt_arg(a: ast.arg) -> str:
        nonlocal has_any_cython_sig_type

        if a.annotation is not None:
            ctype = _match_cython_type_expr(src, a.annotation, alias, type_map)
            if ctype is not None:
                has_any_cython_sig_type = True
                return f"{ctype} {a.arg}"

        return a.arg

    pos_args = list(func.args.args)
    defaults = list(func.args.defaults)
    n_pos = len(pos_args)
    n_def = len(defaults)
    first_def = n_pos - n_def

    parts: List[str] = []

    for idx, a in enumerate(pos_args):
        p = fmt_arg(a)
        if idx >= first_def:
            d = defaults[idx - first_def]
            parts.append(f"{p}={_unparse(d)}")
        else:
            parts.append(p)

    if func.args.vararg is not None:
        parts.append(f"*{func.args.vararg.arg}")
    elif func.args.kwonlyargs:
        parts.append("*")

    for a, d in zip(func.args.kwonlyargs, func.args.kw_defaults):
        p = fmt_arg(a)
        if d is None:
            parts.append(p)
        else:
            parts.append(f"{p}={_unparse(d)}")

    if func.args.kwarg is not None:
        parts.append(f"**{func.args.kwarg.arg}")

    ret_prefix = "cpdef"
    if func.returns is not None:
        rtype = _match_cython_type_expr(src, func.returns, alias, type_map)
        if rtype is not None:
            has_any_cython_sig_type = True
            ret_prefix = f"cpdef {rtype}"

    if not has_any_cython_sig_type and not force_cpdef:
        return None

    indent = " " * func.col_offset
    args_s = ", ".join(parts)
    return f"{indent}{ret_prefix} {func.name}({args_s}):"


def _build_cdef_for_annassign(
    src: str,
    node: ast.AnnAssign,
    alias: str,
    type_map: Mapping[str, str],
) -> Optional[str]:
    if not isinstance(node.target, ast.Name):
        return None

    if node.annotation is None:
        return None

    ctype = _match_cython_type_expr(src, node.annotation, alias, type_map)
    if ctype is None:
        return None

    line0 = src.splitlines(keepends=True)[node.lineno - 1]

    # 关键修复：
    # 无论替换范围从哪开始，缩进都必须用原语句真实缩进，而不是 s_col。
    indent = line0[:node.col_offset]

    name = node.target.id
    if node.value is None:
        return f"{indent}cdef {ctype} {name}"
    return f"{indent}cdef {ctype} {name} = {_unparse(node.value)}"


def cythonize_annotations(
    source,
    alias: str = ALIAS,
    type_map: Optional[Mapping[str, str]] = None,
    force_cpdef: bool = False,
) -> str:
    src = _ensure_text_source(source)

    if type_map is None:
        type_map = DEFAULT_TYPE_MAP

    tree = ast.parse(src, type_comments=True)
    starts = _line_starts(src)

    func_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    min_func_col = min((n.col_offset for n in func_nodes), default=0)

    edits: List[_Edit] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node, "end_lineno", None) is not None:
            repl = _build_cdef_for_annassign(src, node, alias, type_map)
            if repl is None:
                continue

            line0 = src.splitlines(keepends=True)[node.lineno - 1]
            before = line0[:node.col_offset]

            # 如果前面只有空白，就整行替换，从列 0 开始；
            # 但 replacement 里会自己带回正确缩进。
            s_col = 0 if before.strip() == "" else node.col_offset

            s = _abs_index(starts, node.lineno, s_col)
            e = _abs_index(starts, node.end_lineno, node.end_col_offset)
            edits.append(_Edit(s, e, repl))

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            force_this = force_cpdef and (node.col_offset == min_func_col)

            new_header = _build_cpdef_header(src, node, alias, type_map, force_this)
            if new_header is None:
                continue

            span = _find_def_header_span(src, node)
            if span is None:
                continue

            s, e = span
            edits.append(_Edit(s, e, new_header))

    edits.sort(key=lambda x: x.start, reverse=True)

    out = src
    for ed in edits:
        out = out[:ed.start] + ed.replacement + out[ed.end:]

    return out


def transpile(source, alias: str = ALIAS, force_cpdef: bool = False) -> str:
    return cythonize_annotations(
        source,
        alias=alias,
        type_map=dict(DEFAULT_TYPE_MAP),
        force_cpdef=force_cpdef,
    )
