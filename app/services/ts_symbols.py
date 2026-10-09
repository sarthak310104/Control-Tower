"""
Symbol extraction for non-Python languages, using tree-sitter.

Produces the same shape as ast_conflict.extract_symbols for Python: a dict of
dotted qualified name -> (line_start, line_end), 1-based and inclusive, so
the conflict logic doesn't care which language a file is in.

What counts as a claimable symbol, per language:

- JavaScript / TypeScript / TSX: function and generator declarations,
  classes (incl. abstract), interfaces, enums, TS namespaces, class methods,
  class fields holding a function, and `const x = () => ...` /
  `const x = function () {}` / `const X = class {}` bindings. A wrapping
  `export` is included in the span. Nested symbols get dotted names
  (`Cart.total`, `outer.inner`), exactly like the Python extractor.
- Go: functions, methods (named `Receiver.Method`, e.g. `Svc.Create`) and
  type declarations. Go methods live outside their type's braces, so a claim
  on `Svc` covers the type declaration only, not its methods.
- Java: classes, interfaces, enums, records, methods and constructors.

Overloads (Java, TS) share one name; their spans are merged into one
range covering every overload, the conservative choice for conflict checks.

A file that tree-sitter can't parse cleanly (realistically: mid-edit) raises
UnparsableFileError, the same contract as Python files.
"""

from dataclasses import dataclass
from functools import lru_cache

try:
    import tree_sitter as _ts
except ImportError:  # pragma: no cover - only when the optional dependency is missing
    _ts = None


class UnsupportedLanguageError(Exception):
    pass


@dataclass(frozen=True)
class Span:
    line_start: int
    line_end: int


EXTENSIONS = {
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
}


def language_for(file_path: str | None) -> str | None:
    """'python', a tree-sitter language name, or None if symbol claims aren't supported for this file."""
    if file_path is None or file_path.endswith((".py", ".pyi")):
        return "python"
    dot = file_path.rfind(".")
    return EXTENSIONS.get(file_path[dot:].lower()) if dot != -1 else None


@lru_cache(maxsize=None)
def _parser(lang: str):
    if _ts is None:
        raise UnsupportedLanguageError("tree-sitter is not installed; only Python files support symbol claims")
    try:
        if lang == "javascript":
            import tree_sitter_javascript as m
            language = m.language()
        elif lang in ("typescript", "tsx"):
            import tree_sitter_typescript as m
            language = m.language_typescript() if lang == "typescript" else m.language_tsx()
        elif lang == "go":
            import tree_sitter_go as m
            language = m.language()
        elif lang == "java":
            import tree_sitter_java as m
            language = m.language()
        else:
            raise UnsupportedLanguageError(f"no grammar for {lang}")
    except ImportError as exc:
        raise UnsupportedLanguageError(f"the tree-sitter grammar for {lang} is not installed") from exc
    return _ts.Parser(_ts.Language(language))


# ---------------------------------------------------------------- per language

_FUNC_VALUES = {"arrow_function", "function_expression", "function", "generator_function", "class"}
_JS_DECLS = {
    "function_declaration", "generator_function_declaration", "class_declaration",
    "abstract_class_declaration", "interface_declaration", "enum_declaration", "type_alias_declaration",
}
_JS_MEMBERS = {"method_definition", "abstract_method_signature"}


def _text(node) -> str:
    return node.text.decode("utf-8", "replace")


def _js_symbol(node):
    """
    (name, span_node, body_node) if node declares a JS/TS symbol, else None.
    span_node gives the claimed lines; body_node is where nested symbols are
    looked for (each declaration is walked exactly once).
    """
    t = node.type
    if t == "export_statement":
        decl = node.child_by_field_name("declaration")
        if decl is None:  # `export default class X {}` has no declaration field
            decl = next((c for c in node.named_children if c.type in _JS_DECLS), None)
        if decl is not None:
            inner = _js_symbol(decl)
            if inner is not None:
                return inner[0], node, inner[2]  # span widened to include `export`; walk the declaration once
        return None
    if t in _JS_DECLS or t in _JS_MEMBERS:
        name = node.child_by_field_name("name")
        return (_text(name), node, node) if name is not None else None
    if t in ("internal_module", "module"):
        name = node.child_by_field_name("name")
        return (_text(name), node, node) if name is not None else None
    if t in ("lexical_declaration", "variable_declaration"):
        decls = [c for c in node.named_children if c.type == "variable_declarator"]
        if len(decls) == 1:
            value = decls[0].child_by_field_name("value")
            name = decls[0].child_by_field_name("name")
            if value is not None and value.type in _FUNC_VALUES and name is not None and name.type == "identifier":
                return _text(name), node, value
        return None
    if t in ("public_field_definition", "field_definition"):
        value = node.child_by_field_name("value")
        name = node.child_by_field_name("name") or node.child_by_field_name("property")
        if value is not None and value.type in _FUNC_VALUES and name is not None:
            return _text(name), node, value
    return None


def _go_receiver_type(node) -> str | None:
    recv = node.child_by_field_name("receiver")
    if recv is None:
        return None
    for param in recv.named_children:
        typ = param.child_by_field_name("type")
        while typ is not None and typ.type in ("pointer_type", "generic_type", "parenthesized_type"):
            typ = typ.child_by_field_name("type") or (typ.named_children[0] if typ.named_children else None)
        if typ is not None:
            return _text(typ)
    return None


def _go_symbol(node):
    t = node.type
    if t == "function_declaration":
        name = node.child_by_field_name("name")
        return (_text(name), node, node) if name is not None else None
    if t == "method_declaration":
        name = node.child_by_field_name("name")
        if name is None:
            return None
        recv = _go_receiver_type(node)
        return (f"{recv}.{_text(name)}" if recv else _text(name)), node, node
    if t == "type_declaration":
        specs = [c for c in node.named_children if c.type in ("type_spec", "type_alias")]
        if len(specs) == 1:
            name = specs[0].child_by_field_name("name")
            return (_text(name), node, specs[0]) if name is not None else None
    if t in ("type_spec", "type_alias") and node.parent is not None and len(
        [c for c in node.parent.named_children if c.type in ("type_spec", "type_alias")]
    ) > 1:
        name = node.child_by_field_name("name")
        return (_text(name), node, node) if name is not None else None
    return None


_JAVA_DECLS = {
    "class_declaration", "interface_declaration", "enum_declaration", "record_declaration",
    "annotation_type_declaration", "method_declaration", "constructor_declaration",
}


def _java_symbol(node):
    if node.type in _JAVA_DECLS:
        name = node.child_by_field_name("name")
        return (_text(name), node, node) if name is not None else None
    return None


_RULES = {"javascript": _js_symbol, "typescript": _js_symbol, "tsx": _js_symbol, "go": _go_symbol, "java": _java_symbol}


def _span(node) -> tuple[int, int]:
    start = node.start_point[0] + 1
    end_row, end_col = node.end_point
    end = end_row if end_col == 0 and end_row > node.start_point[0] else end_row + 1
    return start, max(start, end)


def extract(source: str, lang: str) -> dict[str, Span]:
    """Qualified name -> Span for every claimable symbol. Raises SyntaxError if the file doesn't parse cleanly."""
    tree = _parser(lang).parse(source.encode("utf-8"))
    if tree.root_node.has_error:
        raise SyntaxError(f"{lang} source has syntax errors")
    rule = _RULES[lang]
    out: dict[str, Span] = {}

    def visit(node, prefix: str) -> None:
        for child in node.named_children:
            hit = rule(child)
            if hit is None:
                visit(child, prefix)
                continue
            name, span_node, body = hit
            qualified = name if (prefix == "" or lang == "go") else f"{prefix}.{name}"
            start, end = _span(span_node)
            if qualified in out:  # overloads: one claim covers all of them
                prev = out[qualified]
                start, end = min(start, prev.line_start), max(end, prev.line_end)
            out[qualified] = Span(start, end)
            visit(body, qualified)

    visit(tree.root_node, "")
    return out