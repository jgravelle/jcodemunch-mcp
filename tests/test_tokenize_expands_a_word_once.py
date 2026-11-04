"""A word is expanded once per process, not once per occurrence.

The first search on a large index builds the BM25 table, which tokenizes every
symbol's name, signature, summary and docstring. Before this, each occurrence of
a word re-ran its stem, its abbreviation lookup and its CJK check: on a
50,373-symbol index, `_stem` ran 922,862 times for 9,633 distinct words. The
expansion is a pure function of the lowercased word, so it is cached, and the
tokenizer's output must not change by a single token.
"""

from __future__ import annotations

import re

import pytest

from jcodemunch_mcp.retrieval import scoring


def _reference_tokenize(text: str) -> list[str]:
    """The tokenizer body as it stood before the cache, frozen for comparison."""
    if not text:
        return []
    text = scoring._CAMEL_RE.sub(r"\1_\2", text)
    text = scoring._CJK_RE.sub(lambda m: " " + m.group(0) + " ", text)
    raw_tokens = [t.lower() for t in scoring._TOKEN_RE.findall(text)]
    result: list[str] = []
    seen: set[str] = set()
    for tok in raw_tokens:
        if scoring._CJK_RE.fullmatch(tok):
            for bg in scoring._cjk_bigrams(tok):
                result.append(bg)
                seen.add(bg)
            continue
        if len(tok) < 2:
            continue
        result.append(tok)
        seen.add(tok)
        stemmed = scoring._stem(tok)
        if stemmed != tok and stemmed not in seen:
            result.append(stemmed)
            seen.add(stemmed)
        for key in (tok, stemmed) if stemmed != tok else (tok,):
            for exp in scoring._ABBREV_MAP.get(key, ()):
                if exp not in seen:
                    result.append(exp)
                    seen.add(exp)
    return result


_CASES = [
    "",
    "a",
    "getUserById",
    "parse_config_files parse parsing parsed",
    "HTTPServerRequestHandler",
    "def connect(self, cfg: Config, retries: int = 3) -> Connection",
    "Returns the authenticated user's settings, or None if missing.",
    "auth auth authentication cfg config configuration",
    "repeated repeated repeated words words",
    "안녕하세요 세계",
    "数据库连接 database connection",
    "café naïve résumé",
    "x_y_z a1 b2 snake_case_name CamelCaseName",
    "running stopped mapping dragged planned",
    "classes processes buses status",
]

# Every word the abbreviation map knows, alone and beside its expansion, so the
# seen-set dedup across a word and its expansions is exercised for each entry.
_ABBREV_CASES = [
    f"{key} {' '.join(exps)} {key}" for key, exps in sorted(scoring._ABBREV_MAP.items())
]


@pytest.mark.parametrize("text", _CASES + _ABBREV_CASES)
def test_tokenize_matches_the_uncached_tokenizer(text):
    assert scoring._tokenize(text) == _reference_tokenize(text)


def test_tokenize_matches_on_a_mixed_corpus():
    words = re.findall(r"\w+", __doc__ + " ".join(_CASES + _ABBREV_CASES))
    corpus = [" ".join(words[i : i + 7]) for i in range(0, len(words), 3)]
    for text in corpus:
        assert scoring._tokenize(text) == _reference_tokenize(text), text


def test_a_repeated_word_is_not_stemmed_again(monkeypatch):
    text = "tokenizing repeated identifiers repeatedly configuration"
    scoring._tokenize(text)
    calls: list[str] = []
    real = scoring._stem

    def counting(word: str) -> str:
        calls.append(word)
        return real(word)

    monkeypatch.setattr(scoring, "_stem", counting)
    scoring._tokenize(text)
    assert calls == [], f"re-stemmed {calls} on a second pass over the same words"


def test_the_word_cache_is_bounded():
    info = scoring._expand_word.cache_info()
    assert info.maxsize is not None and 0 < info.maxsize <= 1 << 20
