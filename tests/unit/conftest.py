"""Root conftest — make the vendored ``unifideck`` package importable.

The plugin package lives in ``py_modules/unifideck/`` (a non-src,
non-installed layout dictated by Decky Loader). It is never
``pip install``-ed, so ``import unifideck`` fails during test
collection unless ``py_modules/`` is on ``sys.path``.

Why this lives in a root ``conftest.py`` and not in
``[tool.pytest.ini_options] pythonpath`` in ``pyproject.toml``:

pytest loads exactly ONE config file, by priority
(``pytest.ini`` > ``pyproject.toml`` > ``tox.ini`` >
``setup.cfg``). The repository ships a ``pytest.ini``, so
pytest reports ``configfile: pytest.ini`` and the
``[tool.pytest.ini_options]`` table in ``pyproject.toml`` —
including any ``pythonpath`` set there — is **never read**. A
``conftest.py`` at the rootdir, by contrast, is imported by
pytest *before* collection regardless of which config file
wins, so the path injection here is robust to that priority
rule. This is the canonical fix for a non-installed,
non-src layout.

Kept dependency-free and idempotent on purpose: it must run
before any project import and must not break if pytest discovers
it twice.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PY_MODULES = Path(__file__).resolve().parent / "py_modules"

if _PY_MODULES.is_dir():
    _entry = str(_PY_MODULES)
    if _entry not in sys.path:
        # Prepend so the vendored package wins over any
        # like-named site-packages shadow.
        sys.path.insert(0, _entry)
