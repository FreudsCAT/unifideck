"""The shipped defaults must satisfy their own config schema.

Regression guard for the class of bug where ``defaults/config.json``
is changed but ``py_modules/unifideck/config/schema.json`` is not, so
the plugin fails validation at boot on EVERY install and drops into
"degraded mode". This actually shipped: a commit switched
``ui.locale`` to the ``"auto"`` sentinel, but the schema pattern
``^[a-z]{2}(-[A-Z]{2})?$`` rejected ``"auto"``.

Kept jsonschema-free on purpose: it walks the schema's ``pattern``
constraints and checks the shipped default values against them with
``re`` directly, so it guards the regression in any environment
(the vendored jsonschema is not importable under every local Python
ABI). ``ui.locale == "auto"`` is asserted explicitly as the specific
value that regressed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest


def _load(relative: str) -> Any:
    # Imported lazily (not at module top level) so it resolves during
    # test *execution* rather than *collection*: ``tests/unit/`` has no
    # ``__init__.py``, so the dotted ``tests.unit._repo_root`` import is
    # only reliable once pytest has finished setting up sys.path. A
    # collection-time top-level import raised ``ModuleNotFoundError: No
    # module named 'tests'`` under CI's Python 3.12. Mirrors the lazy
    # import already used by test_validate_event_schemas.py and
    # _tooling/test_flake8_config.py.
    from tests.unit._repo_root import find_repo_file

    path = find_repo_file(relative)
    if path is None:  # environment problem, but surface loudly (no skip)
        pytest.fail(
            f"could not locate {relative} from the repo root — "
            "test environment is missing the checkout",
        )
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _iter_pattern_constraints(
    schema_node: Any, data_node: Any, path: str,
) -> list[tuple[str, str, str]]:
    """Walk schema ``properties`` in lockstep with the data.

    Yields ``(dotted_path, value, pattern)`` for every leaf where the
    schema declares a string ``pattern`` and the data supplies a value.
    Only descends into ``properties`` objects — enough to cover
    ``ui.locale`` and the other flat pattern-constrained keys without
    needing a full JSON Schema engine.
    """
    found: list[tuple[str, str, str]] = []
    if not isinstance(schema_node, dict):
        return found

    pattern = schema_node.get("pattern")
    if (
        isinstance(pattern, str)
        and isinstance(data_node, str)
    ):
        found.append((path, data_node, pattern))

    props = schema_node.get("properties")
    if isinstance(props, dict) and isinstance(data_node, dict):
        for key, sub_schema in props.items():
            if key in data_node:
                child = f"{path}.{key}" if path else key
                found.extend(
                    _iter_pattern_constraints(
                        sub_schema, data_node[key], child,
                    ),
                )
    return found


def test_shipped_defaults_satisfy_schema_patterns():
    """Every pattern-constrained default value matches its pattern."""
    schema = _load("py_modules/unifideck/config/schema.json")
    defaults = _load("defaults/config.json")

    constraints = _iter_pattern_constraints(
        schema, defaults, path="",
    )
    # Sanity: we actually exercised at least the ui.locale key.
    assert any(p == "ui.locale" for p, _, _ in constraints), (
        "ui.locale not covered — schema/defaults structure changed"
    )

    violations = [
        (p, value, pattern)
        for p, value, pattern in constraints
        if re.match(pattern, value) is None
    ]
    assert not violations, (
        "shipped defaults violate their own schema pattern(s): "
        + "; ".join(
            f"{p}={value!r} !~ {pattern!r}"
            for p, value, pattern in violations
        )
    )


def test_ui_locale_default_is_auto_and_allowed():
    """The specific value that regressed: ui.locale defaults to the
    'auto' sentinel and the schema pattern permits it."""
    schema = _load("py_modules/unifideck/config/schema.json")
    defaults = _load("defaults/config.json")

    assert defaults["ui"]["locale"] == "auto"
    pattern = schema["properties"]["ui"]["properties"]["locale"]["pattern"]
    assert re.match(pattern, "auto") is not None
    # A concrete BCP-47 tag must still validate…
    assert re.match(pattern, "en-US") is not None
    # …and garbage must still be rejected.
    assert re.match(pattern, "not-a-locale") is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
