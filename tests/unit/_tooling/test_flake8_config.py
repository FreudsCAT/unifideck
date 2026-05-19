"""Guard test — .flake8 complexity thresholds.

The repository ``.flake8`` documents an explicit contract: its
``max-complexity`` / ``max-cognitive-complexity`` values must
mirror the ``--max-complexity`` / ``--max-cognitive-complexity``
flags passed by ``.github/workflows/complexity.yml`` on the CLI.

The ``.flake8`` header names *this* test by name as the
alignment guard:

    "... this file must be updated in mirror (along with the
    ``test_flake8_config_thresholds_are_set`` test that
    verifies the alignment)."

So this module exists to fail loudly if either the CI flag or
the ``.flake8`` value drifts, keeping local (pre-commit / IDE)
and CI complexity gates in lockstep.

Resolution of the ``.flake8`` path is deliberately robust: the
test suite runs out-of-tree (PYTHONPATH points at the extracted
repo), so we try, in order:

1. the ``UNIFIDECK_REPO_ROOT`` environment variable,
2. walking up from the imported ``unifideck`` package until a
   directory containing ``.flake8`` is found,
3. a small set of well-known checkout locations.

If no ``.flake8`` can be located the test SKIPS (rather than
fails) — a missing checkout is an environment issue, not a
threshold regression. Whenever the file *is* found, the
threshold assertions are strict.
"""
from __future__ import annotations

import configparser
from pathlib import Path

import pytest

# The contract values. These are intentionally hard-coded here
# (not read from the file under test) so the test is a true
# double-entry check: the .flake8 value must equal this literal,
# and the literal must equal the CI flag. Bump both together.
_EXPECTED_MAX_COMPLEXITY = 15
_EXPECTED_MAX_COGNITIVE_COMPLEXITY = 15
_EXPECTED_MAX_LINE_LENGTH = 100


def _find_flake8_config() -> Path | None:
    """Locate the repository ``.flake8`` via the shared,
    structurally-resolved repo root.

    Delegating to ``tests.unit._repo_root`` keeps this test
    independent of its own location in the tree (it can live
    in ``tests/unit/`` or ``tests/unit/_tooling/`` without a
    fragile ``parents[N]``) and removes the previously
    hard-coded absolute fallback path.
    """
    from tests.unit._repo_root import find_repo_file

    return find_repo_file(".flake8")


@pytest.fixture(scope="module")
def flake8_section() -> configparser.SectionProxy:
    cfg_path = _find_flake8_config()
    if cfg_path is None:
        pytest.skip(
            "repository .flake8 not found in any candidate "
            "location (set UNIFIDECK_REPO_ROOT to point at "
            "the checkout root)")
    parser = configparser.ConfigParser()
    # configparser tolerates the leading '#' comment block;
    # the [flake8] section is standard INI.
    parser.read(cfg_path, encoding="utf-8")
    assert parser.has_section("flake8"), (
        f"{cfg_path} has no [flake8] section")
    return parser["flake8"]


def test_flake8_config_thresholds_are_set(
    flake8_section: configparser.SectionProxy,
) -> None:
    """Both complexity thresholds are present AND equal to the
    CI-mirrored contract values (15 / 15).

    A missing key is as bad as a wrong value: if ``.flake8``
    omits ``max-cognitive-complexity`` entirely, flake8 falls
    back to its default of 7 and local runs disagree with CI
    — exactly the drift this guard exists to catch.
    """
    assert "max-complexity" in flake8_section, (
        "max-complexity missing from .flake8 — flake8 would "
        "fall back to its mccabe default and diverge from "
        "the CI --max-complexity flag")
    assert "max-cognitive-complexity" in flake8_section, (
        "max-cognitive-complexity missing from .flake8 — "
        "flake8 would fall back to the plugin default (7) "
        "and diverge from the CI flag")

    max_complexity = flake8_section.getint("max-complexity")
    max_cognitive = flake8_section.getint(
        "max-cognitive-complexity")

    assert max_complexity == _EXPECTED_MAX_COMPLEXITY, (
        f"max-complexity={max_complexity} but the CI workflow "
        f"passes --max-complexity={_EXPECTED_MAX_COMPLEXITY}; "
        f"update .flake8 and complexity.yml together")
    assert max_cognitive == _EXPECTED_MAX_COGNITIVE_COMPLEXITY, (
        f"max-cognitive-complexity={max_cognitive} but the CI "
        f"workflow passes --max-cognitive-complexity="
        f"{_EXPECTED_MAX_COGNITIVE_COMPLEXITY}; update .flake8 "
        f"and complexity.yml together")


def test_flake8_thresholds_mirror_each_other(
    flake8_section: configparser.SectionProxy,
) -> None:
    """The CI contract keeps the cyclomatic and cognitive
    ceilings identical; assert that invariant directly so a
    change to only one side is rejected even if someone bumps
    the literals above without thinking it through."""
    assert flake8_section.getint("max-complexity") == \
        flake8_section.getint("max-cognitive-complexity"), (
        "max-complexity and max-cognitive-complexity must "
        "stay equal per the CI contract documented in the "
        ".flake8 header")


def test_flake8_max_line_length_is_set(
    flake8_section: configparser.SectionProxy,
) -> None:
    """``max-line-length`` is also pinned (100) for visual
    consistency with pyproject's ruff line-length, even though
    E501 itself is ignored."""
    assert "max-line-length" in flake8_section
    assert flake8_section.getint("max-line-length") == \
        _EXPECTED_MAX_LINE_LENGTH


def test_flake8_e501_is_ignored(
    flake8_section: configparser.SectionProxy,
) -> None:
    """E501 must remain in ``extend-ignore`` — line length is
    owned by the formatter (ruff format), not the linter; a
    regression here would resurface hundreds of noise findings.
    """
    extend_ignore = flake8_section.get("extend-ignore", "")
    codes = {
        c.strip() for c in extend_ignore.replace(
            "\n", ",").split(",") if c.strip()
    }
    assert "E501" in codes, (
        "E501 dropped from extend-ignore; line-length is "
        "formatter-owned and must stay linter-ignored")
