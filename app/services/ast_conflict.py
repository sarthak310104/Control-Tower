"""
The core hard problem of this project: given a file's CURRENT content and a
requested symbol (or explicit line range), determine whether it overlaps
with another agent's active claim.

Deliberately pure -- no DB, no I/O beyond what's passed in -- so this is
testable in isolation and the algorithmic correctness doesn't depend on
Postgres being up. services/claims.py is the thin DB-touching orchestration
layer that calls into this.

Python is parsed with the stdlib `ast` module; JavaScript, TypeScript, TSX,
Go and Java with tree-sitter (services/ts_symbols.py). The language comes
from the file's extension; with no path given, Python is assumed.
"""

import ast
from dataclasses import dataclass

from app.services import ts_symbols
from app.services.ts_symbols import UnsupportedLanguageError, language_for  # noqa: F401  (re-exported)


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


def extract_symbols(source: str, file_path: str | None = None) -> dict[str, SymbolRange]:
    """
    Returns qualified_name -> SymbolRange for every claimable symbol in
    `source`. Nesting uses dotted notation: "ClassA.method_b",
    "outer_func.inner_closure".

    The language is picked from `file_path`'s extension (Python when None).
    Raises UnsupportedLanguageError for languages without symbol support
    (claim a line range there) and UnparsableFileError when the file doesn't
    parse cleanly (realistically: someone is mid-edit).
    """
    lang = language_for(file_path)
    if lang is None:
        ext = file_path.rsplit(".", 1)[-1] if file_path and "." in file_path else "these"
        raise UnsupportedLanguageError(
            f"symbol claims aren't supported for .{ext} files; claim a line range instead"
        )
    if lang != "python":
        try:
            spans = ts_symbols.extract(source, lang)
        except SyntaxError as exc:
            raise UnparsableFileError(f"file has {lang} syntax errors: {exc}") from exc
        return {name: SymbolRange(name, s.line_start, s.line_end) for name, s in spans.items()}

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
    file_path: str | None = None,
) -> tuple[int, int]:
    """
    Resolves a claim request to a concrete (line_start, line_end), freshly
    computed against `source` -- the file's CURRENT content, never a cached
    version from claim time. This is what keeps conflict checks accurate as
    code shifts around a claimed symbol.

    Exactly one of symbol_name or (line_start, line_end) must be usable:
    - symbol_name given: re-parse `source`, look it up. Raises
      SymbolNotFoundError if it's gone (renamed/deleted since claimed),
      UnparsableFileError if the file currently doesn't parse (likely
      another agent mid-edit), or UnsupportedLanguageError for a language
      without symbol support -- callers suggest a line range for the last two.
    - line_start/line_end given: used as-is, no parsing needed. This works
      for any file, in any language.
    """
    if symbol_name is not None:
        symbols = extract_symbols(source, file_path)
        if symbol_name not in symbols:
            names = sorted(symbols)
            available = ", ".join(names[:25]) + (f" and {len(names) - 25} more" if len(names) > 25 else "") or "none"
            raise SymbolNotFoundError(
                f"symbol '{symbol_name}' not found in current file content (available: {available})"
            )
        rng = symbols[symbol_name]
        return rng.line_start, rng.line_end
    if line_start is not None and line_end is not None:
        if line_start < 1 or line_end < line_start:
            raise ValueError("line range must satisfy 1 <= line_start <= line_end")
        return line_start, line_end
    raise ValueError("must provide either symbol_name or both line_start and line_end")


def ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """Closed-interval overlap: [a_start, a_end] and [b_start, b_end] share at least one line."""
    return a_start <= b_end and b_start <= a_end