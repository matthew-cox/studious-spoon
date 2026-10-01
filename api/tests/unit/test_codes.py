import random
import string

import pytest

from shortener_api.codes import (
    ALPHABET,
    CODE_LENGTH,
    CODE_PATTERN,
    RESERVED_WORDS,
    generate_code,
    is_reserved,
)


def test_alphabet_is_base62():
    assert string.ascii_letters + string.digits == ALPHABET


def test_generates_seven_base62_characters():
    code = generate_code(random.Random(1))
    assert len(code) == CODE_LENGTH == 7
    assert CODE_PATTERN.match(code)


def test_same_seed_same_code():
    assert generate_code(random.Random(42)) == generate_code(random.Random(42))


def test_reserved_words_are_skipped_case_insensitively(scripted_random):
    assert generate_code(scripted_random(["HealthZ", "abcdefg"])) == "abcdefg"


@pytest.mark.parametrize("word", ["healthz", "OpenAPI", "favicon", "api", "DOCS"])
def test_is_reserved(word):
    assert is_reserved(word)


def test_ordinary_code_is_not_reserved():
    assert not is_reserved("aZ3kQ9x")


def test_reserved_words_match_the_spec():
    expected = {
        "api", "admin", "docs", "redoc", "openapi",
        "healthz", "readyz", "static", "favicon", "robots",
    }  # fmt: skip
    assert expected == RESERVED_WORDS


@pytest.mark.parametrize("code", ["aZ3kQ9x", "a", "x" * 32])
def test_code_pattern_accepts(code):
    assert CODE_PATTERN.match(code)


@pytest.mark.parametrize("code", ["", "bad-code!", "x" * 33, "abc def", "ü"])
def test_code_pattern_rejects(code):
    assert not CODE_PATTERN.match(code)
