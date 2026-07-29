"""unifiDB shard selection must match how the catalog actually buckets.

Reported: The Witcher 3 (GOG) showed "Cloud saves: Unknown" despite the
catalog carrying ``cloud: {epic, gog, steam}`` for it.

``get_first_char_for_bucket`` stripped a leading "the "/"a "/"an " before
taking the two-char shard name, so it asked for ``games/w/wi.json`` while the
record lives in ``games/t/th.json`` — the catalog buckets by the RAW title.
Measured against the live catalog: ``t/th.json`` holds 18,258 records, 16,391
of which begin with "The ". So every article-prefixed title silently lost ALL
unifiDB enrichment — cloud-save support, save locations, descriptions.
"""
from __future__ import annotations

import pytest

from unifideck.metadata.unifidb import get_first_char_for_bucket


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("The Witcher 3: Wild Hunt", "th"),
        ("A Plague Tale: Innocence", "ap"),
        ("I Am Setsuna", "ia"),
        ("An Untitled Story", "an"),
        # Unaffected titles must keep the same shard as before.
        ("Bastion", "ba"),
        ("Spelunky", "sp"),
        ("Witcher 3", "wi"),
    ],
)
def test_bucket_keeps_leading_article(title: str, expected: str) -> None:
    assert get_first_char_for_bucket(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # Digit-leading titles shard exactly like letters. These previously
        # went to a "0_9" bucket whose URL (games/0/0_9.json) 404s.
        ("2064: Read Only Memories", "20"),
        ("1000xRESIST", "10"),
        ("7 Days to Die", "7d"),
        ("33 Immortals", "33"),
    ],
)
def test_digit_leading_titles_shard_like_letters(
    title: str, expected: str,
) -> None:
    assert get_first_char_for_bucket(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("The Witcher 3: Wild Hunt", "wi"),
        ("A Plague Tale: Innocence", "pl"),
        ("Bastion", "ba"),
        ("2064: Read Only Memories", "20"),
    ],
)
def test_strip_article_variant_is_still_available(
    title: str, expected: str,
) -> None:
    """``lookup`` tries this as a second shard, so it must stay correct."""
    assert get_first_char_for_bucket(title, strip_article=True) == expected


def test_article_only_title_does_not_crash() -> None:
    """A bare article has no remainder to strip — must not IndexError."""
    assert get_first_char_for_bucket("The", strip_article=True) == "th"
    assert get_first_char_for_bucket("A", strip_article=True) == "aa"


def test_titles_with_no_alphanumerics_fall_back() -> None:
    assert get_first_char_for_bucket("") == "0_9"
    assert get_first_char_for_bucket("!!!") == "0_9"


def test_single_letter_title_doubles_the_char() -> None:
    assert get_first_char_for_bucket("Q") == "qq"
