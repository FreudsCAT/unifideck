"""Shared repo-root resolution for the test suite.

The suite runs out-of-tree (PYTHONPATH points at the extracted
package, the working directory varies between CI, a local
clone, a git worktree, or an unpacked archive). Tests that need
a file from the *repository* — ``.flake8``, ``scripts/...``,
``defaults/config.json`` — must locate the checkout root
without ever hard-coding an absolute path.

Resolution is purely structural: a directory is the repo root
iff it contains the ``py_modules`` package together with
``pyproject.toml`` or ``.flake8``. That recognises a checkout
wherever it lives on disk and avoids mistaking an unrelated
ancestor (N levels up) for the root.

Lookup order, most-authoritative first:
  1. ``UNIFIDECK_REPO_ROOT`` env var (explicit override);
  2. ancestors of the imported ``unifideck`` package
     (layout: ``<root>/py_modules/unifideck/``);
  3. ancestors of this helper file;
  4. the cwd and its ancestors.
"""
from __future__ import annotations

import os
from pathlib import Path


def looks_like_repo_root(path: Path) -> bool:
    """True if ``path`` carries the project markers: the
    ``py_modules`` package plus ``pyproject.toml`` or
    ``.flake8``."""
    try:
        return (
            (path / "py_modules").is_dir()
            and (
                (path / "pyproject.toml").is_file()
                or (path / ".flake8").is_file()
            )
        )
    except OSError:  # pragma: no cover - defensive
        return False


def candidate_repo_roots() -> list[Path]:
    """Plausible repo roots, most-authoritative first,
    de-duplicated, never hard-coded."""
    roots: list[Path] = []

    env = os.environ.get("UNIFIDECK_REPO_ROOT")
    if env:
        roots.append(Path(env))

    try:
        import unifideck

        pkg = Path(unifideck.__file__).resolve().parent
        roots.extend(
            p for p in pkg.parents if looks_like_repo_root(p)
        )
    except Exception:  # pragma: no cover - import-env only
        pass

    here = Path(__file__).resolve()
    roots.extend(
        p for p in here.parents if looks_like_repo_root(p)
    )

    cwd = Path.cwd()
    roots.extend(
        p for p in (cwd, *cwd.parents)
        if looks_like_repo_root(p)
    )

    seen: set[Path] = set()
    ordered: list[Path] = []
    for r in roots:
        try:
            rp = r.resolve()
        except OSError:  # pragma: no cover - defensive
            continue
        if rp not in seen:
            seen.add(rp)
            ordered.append(r)
    return ordered


def find_repo_root() -> Path | None:
    """The first candidate that actually looks like a repo
    root, or ``None`` if the checkout can't be located (an
    environment problem, not a regression — callers should
    skip rather than fail)."""
    for root in candidate_repo_roots():
        if looks_like_repo_root(root):
            return root
    return None


def find_repo_file(relative: str) -> Path | None:
    """Resolve ``relative`` (a repo-relative path such as
    ``defaults/config.json`` or ``scripts/x.py``) against the
    located repo root. ``None`` if the root or the file is
    absent."""
    root = find_repo_root()
    if root is None:
        return None
    candidate = root / relative
    try:
        return candidate if candidate.exists() else None
    except OSError:  # pragma: no cover - defensive
        return None
