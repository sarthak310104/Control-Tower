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