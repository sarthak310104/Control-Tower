"""
The core hard problem of this project: given a file's CURRENT content and a
requested symbol (or explicit line range), determine whether it overlaps
with another agent's active claim.

Deliberately pure -- no DB, no I/O beyond what's passed in -- so this is
testable in isolation and the algorithmic correctness doesn't depend on
Postgres being up. services/claims.py is the thin DB-touching orchestration
layer that calls into this.
"""

import ast
from dataclasses import dataclass


class SymbolNotFoundError(Exception):
    pass


class UnparsableFileError(Exception):
    """
    Raised when the file can't be parsed at all -- most commonly because
    another agent is mid-edit and the file is temporarily invalid Python.
    This is a real, expected state, not a bug: the caller should fall back
    to requiring an explicit line range instead of a symbol name when this
    happens (see resolve_range).
    """


@dataclass(frozen=True)
class SymbolRange:
    qualified_name: str
    line_start: int
    line_end: int


def extract_symbols(source: str) -> dict[str, SymbolRange]:
    """
    Parses Python source and returns qualified_name -> SymbolRange for every
    function, async function, class, and method defined in it. Nesting uses
    dotted notation: "ClassA.method_b", "outer_func.inner_closure".

    Scoped to Python only for now (uses the `ast` stdlib module). Other
    languages would need a different parser per-language (tree-sitter is the
    obvious choice for a polyglot version) -- not attempted here; non-Python
    files must use explicit line-range claims instead of symbol_name (see
    resolve_range).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise UnparsableFileError(f"file has invalid Python syntax: {exc}") from exc

    symbols: dict[str, SymbolRange] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified = f"{prefix}.{child.name}" if prefix else child.name
                # end_lineno exists on Python 3.8+; every supported version here has it.
                symbols[qualified] = SymbolRange(qualified, child.lineno, child.end_lineno or child.lineno)
                visit(child, qualified)
            else:
                visit(child, prefix)

    visit(tree, "")
    return symbols


def resolve_range(
    source: str,
    symbol_name: str | None,
    line_start: int | None,
    line_end: int | None,
) -> tuple[int, int]:
    """
    Resolves a claim request to a concrete (line_start, line_end), freshly
    computed against `source` -- the file's CURRENT content, never a cached
    version from claim time. This is what keeps conflict checks accurate as
    code shifts around a claimed symbol.

    Exactly one of symbol_name or (line_start, line_end) must be usable:
    - symbol_name given: re-parse `source`, look it up. Raises
      SymbolNotFoundError if it's gone (renamed/deleted since claimed) or
      UnparsableFileError if the file is currently invalid Python (likely
      another agent mid-edit) -- callers should catch this and suggest an
      explicit line range instead.
    - line_start/line_end given: used as-is, no parsing needed. This is the
      required path for non-Python files.
    """
    if symbol_name is not None:
        symbols = extract_symbols(source)
        if symbol_name not in symbols:
            available = ", ".join(sorted(symbols)) or "none"
            raise SymbolNotFoundError(
                f"symbol '{symbol_name}' not found in current file content (available: {available})"
            )
        rng = symbols[symbol_name]
        return rng.line_start, rng.line_end
    if line_start is not None and line_end is not None:
        return line_start, line_end
    raise ValueError("must provide either symbol_name or both line_start and line_end")


def ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """Closed-interval overlap: [a_start, a_end] and [b_start, b_end] share at least one line."""
    return a_start <= b_end and b_start <= a_end
