"""Tooling tests — audits of linter / CI configuration.

This subpackage holds the tests that audit **configuration
files** (``.flake8``, pre-commit hooks, CI scope…) rather than
the source code of the ``unifideck`` package. The leading ``_``
in the package name is deliberate: it signals that this folder
does NOT mirror a source subpackage (cf. the convention in
``tests/unit/`` which requires
``tests/unit/<sub_package>/test_<source_file>.py``).

Current coverage:
    * ``test_lint_scope`` — invariants on ``.flake8`` and a
      guard against vendored files leaking into the flake8
      scope.
    * ``test_flake8_config`` — complexity thresholds of
      ``.flake8`` (max-complexity / max-cognitive-complexity /
      max-line-length) and their consistency with the CI CLI.
      Complements ``test_lint_scope`` from the angle of the
      declared values.
"""
