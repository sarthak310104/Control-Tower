"""Pure tests for the conflict algorithm: no database, no network."""

import pytest

from app.services.ast_conflict import (
    SymbolNotFoundError,
    UnparsableFileError,
    extract_symbols,
    ranges_overlap,
    resolve_range,
)

SRC = '''\
import os


class Repo:
    def get(self, key):
        return key

    async def put(self, key, value):
        def _validate():
            return True
        return _validate()


def outer():
    def inner():
        pass
    return inner
'''


def test_extracts_dotted_names_with_spans():
    s = extract_symbols(SRC)
    assert set(s) == {"Repo", "Repo.get", "Repo.put", "Repo.put._validate", "outer", "outer.inner"}
    assert (s["Repo"].line_start, s["Repo"].line_end) == (4, 11)
    assert (s["Repo.get"].line_start, s["Repo.get"].line_end) == (5, 6)
    assert (s["outer.inner"].line_start, s["outer.inner"].line_end) == (15, 16)


def test_decorated_function_span_starts_at_def():
    src = "@decorator\ndef f():\n    pass\n"
    assert resolve_range(src, "f", None, None) == (2, 3)


def test_symbol_identity_survives_code_shifting_above_it():
    """The core design property: identity is the name; lines are recomputed against current content."""
    before = resolve_range(SRC, "outer", None, None)
    shifted = "# new header\n" * 7 + SRC
    after = resolve_range(shifted, "outer", None, None)
    assert after == (before[0] + 7, before[1] + 7)


def test_class_span_contains_its_methods():
    cls = resolve_range(SRC, "Repo", None, None)
    method = resolve_range(SRC, "Repo.get", None, None)
    assert ranges_overlap(*cls, *method)


def test_sibling_methods_do_not_overlap():
    assert not ranges_overlap(*resolve_range(SRC, "Repo.get", None, None), *resolve_range(SRC, "Repo.put", None, None))


def test_missing_symbol_lists_what_exists():
    with pytest.raises(SymbolNotFoundError, match="Repo.get"):
        resolve_range(SRC, "Repo.gone", None, None)


def test_invalid_python_is_unparsable_not_missing():
    with pytest.raises(UnparsableFileError):
        resolve_range("def broken(:\n", "broken", None, None)


def test_line_range_needs_no_parsing():
    assert resolve_range("not python at all {{{", None, 3, 9) == (3, 9)


def test_requires_symbol_or_full_range():
    with pytest.raises(ValueError):
        resolve_range(SRC, None, 3, None)


@pytest.mark.parametrize("a,b,expected", [
    ((1, 5), (5, 9), True),    # touching at one line
    ((1, 4), (5, 9), False),   # adjacent, no shared line
    ((3, 3), (1, 10), True),   # contained
])
def test_closed_interval_overlap(a, b, expected):
    assert ranges_overlap(*a, *b) is expected
    assert ranges_overlap(*b, *a) is expected

# ---------------------------------------------------------------- other languages (tree-sitter)

from app.services.ast_conflict import UnsupportedLanguageError, extract_symbols, language_for  # noqa: E402

TSX = """\
import { useState } from "react";

export function Checkout({ cart }) {
  const total = () => cart.items.length;
  return <PriceSummary items={cart.items} total={total()} />;
}

export const Cart = () => null;

export default class CartStore {
  items: string[] = [];
  add(item: string) {
    this.items.push(item);
  }
  clear = () => {
    this.items = [];
  };
}
"""

GO = """\
package store

type Svc struct {
\trepo Repo
}

func (s *Svc) Create(name string) error {
\treturn s.repo.Save(name)
}

func helper() int {
\treturn 42
}
"""

JAVA = """\
public class UserService {
    private final Repo repo;

    public UserService(Repo repo) {
        this.repo = repo;
    }

    public void create(String name) {
        repo.save(name);
    }

    public void create(String name, String email) {
        repo.save(name + email);
    }
}
"""


def spans(src, path):
    return {k: (v.line_start, v.line_end) for k, v in extract_symbols(src, path).items()}


def test_tsx_functions_classes_methods_and_arrow_bindings():
    s = spans(TSX, "web/checkout.tsx")
    assert s["Checkout"] == (3, 6)
    assert s["Checkout.total"] == (4, 4)
    assert s["Cart"] == (8, 8)
    assert s["CartStore"] == (10, 18)
    assert s["CartStore.add"] == (12, 14)
    assert s["CartStore.clear"] == (15, 17)


def test_class_span_contains_methods_in_typescript_too():
    s = spans(TSX, "web/checkout.tsx")
    assert ranges_overlap(*s["CartStore"], *s["CartStore.add"])
    assert not ranges_overlap(*s["CartStore.add"], *s["CartStore.clear"])


def test_go_methods_are_qualified_by_receiver():
    s = spans(GO, "store/svc.go")
    assert s == {"Svc": (3, 5), "Svc.Create": (7, 9), "helper": (11, 13)}


def test_java_overloads_merge_into_one_claimable_span():
    s = spans(JAVA, "src/UserService.java")
    assert s["UserService"] == (1, 15)
    assert s["UserService.UserService"] == (4, 6)
    assert s["UserService.create"] == (8, 14)


def test_plain_javascript_and_identity_survives_shifting():
    js = "function a() {}\n\nconst b = function () {\n  return 1;\n};\n"
    assert resolve_range(js, "b", None, None, "x.js") == (3, 5)
    assert resolve_range("// header\n" * 4 + js, "b", None, None, "x.js") == (7, 9)


def test_broken_typescript_is_unparsable_not_missing():
    with pytest.raises(UnparsableFileError):
        resolve_range("export function f( {\n", "f", None, None, "a.ts")


def test_unsupported_language_asks_for_a_line_range():
    with pytest.raises(UnsupportedLanguageError, match="line range"):
        resolve_range("puts 1\n", "x", None, None, "script.rb")
    assert resolve_range("puts 1\n", None, 1, 1, "script.rb") == (1, 1)


@pytest.mark.parametrize("path,lang", [
    ("a.py", "python"), ("a.tsx", "tsx"), ("a.ts", "typescript"), ("a.jsx", "javascript"),
    ("a.go", "go"), ("A.java", "java"), ("a.rb", None), ("Makefile", None), (None, "python"),
])
def test_language_detection(path, lang):
    assert language_for(path) == lang