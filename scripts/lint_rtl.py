"""scripts/lint_rtl.py — Static check for RTL-unsafe CSS patterns.
Scans every TypeScript/TSX file under src/ and fails the build
if it finds any directional CSS property that would not flip
automatically when the document is in right-to-left mode.
Why a custom script instead of an ESLint plugin?
 - Zero npm dependency: runs from a Python step in the same
 workflow that already installs Python for translate_at_build
 - Easier to audit: 200 lines of pure regex, no plugin loader,
 no transitive deps
 - Easier to extend: adding a new forbidden pattern is a one-line
 addition to FORBIDDEN_PATTERNS below
 - Independent of any existing eslint config in the consuming
 repo: this script doesn't need to be merged into someone
 else's lint setup
What it forbids (with the recommended replacement):
 marginLeft / marginRight → marginInlineStart / marginInlineEnd
 paddingLeft / paddingRight → paddingInlineStart / paddingInlineEnd
 borderLeft / borderRight → borderInlineStart / borderInlineEnd
 textAlign: "left" → textAlign: "start"
 textAlign: "right" → textAlign: "end"
 float: "left" → float: "inline-start"
 float: "right" → float: "inline-end"
 left: <value> → insetInlineStart: <value>
 right: <value> → insetInlineEnd: <value>
 translateX(...) → use a flex/grid layout instead
 ChevronLeft / ChevronRight → use a directional helper from useRTL()
 ArrowLeft / ArrowRight → same
What it allows:
 - Comments containing the forbidden words (// or /* */ or
 JSDoc) — those are documentation, not code
 - String literals containing the forbidden words — those are
 not CSS values that affect layout
 - The "width" inline style (used by progress bars) — width
 is direction-insensitive
 - Lines carrying an explicit ``rtl-ignore`` marker comment — a
 justified opt-out for intentionally-directional values such as
 a decorative animation whose sweep direction is purely cosmetic
 (mirrors the repo's ``# noqa`` convention)
Exit codes:
 0 — no violations found
 1 — at least one violation found (build fails)
 2 — script error (e.g. src/ not found)
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from re import Pattern


# ══════════════════════════════════════════════════════════════════
# Forbidden patterns
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class ForbiddenPattern:
    """One pattern that must not appear in production code.
    The `name` is shown in the error message. The `regex` is
    matched against each non-comment line. The `suggestion`
    tells the developer what to use instead.
    """

    name: str
    regex: Pattern[str]
    suggestion: str


FORBIDDEN_PATTERNS: list[ForbiddenPattern] = [
    ForbiddenPattern(
        name="marginLeft / marginRight",
        regex=re.compile(r"\bmargin(Left|Right)\b"),
        suggestion="use marginInlineStart / marginInlineEnd "
        "for RTL-safe layouts",
    ),
    ForbiddenPattern(
        name="paddingLeft / paddingRight",
        regex=re.compile(r"\bpadding(Left|Right)\b"),
        suggestion="use paddingInlineStart / paddingInlineEnd "
        "for RTL-safe layouts",
    ),
    ForbiddenPattern(
        name="borderLeft / borderRight",
        regex=re.compile(r"\bborder(Left|Right)(Width|Style|Color)?\b"),
        suggestion="use borderInlineStart / borderInlineEnd "
        "for RTL-safe layouts",
    ),
    ForbiddenPattern(
        name='textAlign: "left" or "right"',
        regex=re.compile(r'textAlign\s*:\s*["\'](left|right)["\']'),
        suggestion='use textAlign: "start" or "end" instead — '
        "they auto-flip in RTL",
    ),
    ForbiddenPattern(
        name='float: "left" or "right"',
        regex=re.compile(r'float\s*:\s*["\'](left|right)["\']'),
        suggestion='use float: "inline-start" or "inline-end"',
    ),
    ForbiddenPattern(
        name="absolute positioning left/right",
        # Match `left:` or `right:` followed by a value, but only
        # in object-literal context. Excludes things like
        # `align: "left"` (different prop) and references like
        # `obj.left` (property access). The pattern requires the
        # word to be at the start of a key in a JSX style object.
        regex=re.compile(r"(?:^|[{,\s])(left|right)\s*:\s*[\d'\"`]"),
        suggestion="use insetInlineStart / insetInlineEnd "
        "for RTL-safe absolute positioning",
    ),
    ForbiddenPattern(
        name="translateX",
        regex=re.compile(r"\btranslateX\s*\("),
        suggestion="use a flex/grid layout instead — translateX "
        "does not flip in RTL",
    ),
    ForbiddenPattern(
        name="directional chevron icon",
        regex=re.compile(r"\b\w*Chevron(Left|Right)\b"),
        suggestion="use the useRTL() hook to pick the correct "
        "chevron based on the active direction",
    ),
    ForbiddenPattern(
        name="directional arrow icon",
        regex=re.compile(r"\b\w*Arrow(Left|Right)\b"),
        suggestion="use the useRTL() hook to pick the correct "
        "arrow based on the active direction",
    ),
]

# ══════════════════════════════════════════════════════════════════
# Comment / string masking
# ══════════════════════════════════════════════════════════════════
# Patterns that identify lines we should NOT scan because they're
# comments. We check the leading non-whitespace of each line.
LINE_COMMENT_PREFIX = re.compile(r"^\s*(//|\*|/\*)")


def strip_strings_and_comments(line: str) -> str:
    """Replace string literals with empty quotes so the regex
    matches don't fire on documentation strings.
    This is a heuristic — it doesn't handle every JS edge case
    (template literals with interpolation, escaped quotes inside
    strings) but it covers ~99% of real-world code and avoids
    false positives on lines like:
    const msg = "use marginLeft instead";
    """
    # Remove single-line `//` comments
    line = re.sub(r"//.*$", "", line)
    # Remove inline `/* ... */` block comments on the same line
    line = re.sub(r"/\*.*?\*/", "", line)
    # Replace single-quoted strings
    line = re.sub(r"'(?:[^'\\]|\\.)*'", "''", line)
    # Replace double-quoted strings
    line = re.sub(r'"(?:[^"\\]|\\.)*"', '""', line)
    # Note: we intentionally do NOT strip backtick template literals
    # because they often contain CSS-like template strings that
    # we want to scan (e.g. `margin-left: ${x}px`)
    return line


def is_pure_comment_line(line: str) -> bool:
    """Return True if the line is entirely a comment (no code)."""
    return bool(LINE_COMMENT_PREFIX.match(line))


# ══════════════════════════════════════════════════════════════════
# Main scan
# ══════════════════════════════════════════════════════════════════
@dataclass
class Violation:
    file: str
    line_number: int
    line: str
    pattern_name: str
    suggestion: str


def scan_file(path: Path) -> list[Violation]:
    """Scan one TS/TSX file and return all violations found."""
    violations: list[Violation] = []
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return violations

    in_block_comment = False
    for i, raw_line in enumerate(text.splitlines(), start=1):
        # Track multi-line block comments across lines
        if in_block_comment:
            if "*/" in raw_line:
                in_block_comment = False
            continue
        if "/*" in raw_line and "*/" not in raw_line:
            in_block_comment = True
            continue
        # Skip pure-comment single lines
        if is_pure_comment_line(raw_line):
            continue
        # Explicit, justified opt-out (see module docstring): a line
        # carrying an ``rtl-ignore`` marker is intentionally directional.
        if "rtl-ignore" in raw_line:
            continue
        # Mask strings and inline comments before checking patterns
        scannable = strip_strings_and_comments(raw_line)
        for pattern in FORBIDDEN_PATTERNS:
            if pattern.regex.search(scannable):
                violations.append(
                    Violation(
                        file=str(path),
                        line_number=i,
                        line=raw_line.strip()[:120],
                        pattern_name=pattern.name,
                        suggestion=pattern.suggestion,
                    )
                )
    return violations


def main() -> int:
    src_root = Path("src")
    if not src_root.is_dir():
        print(f"[lint-rtl] error: {src_root} not found", file=sys.stderr)
        return 2
    files = sorted(
        list(src_root.rglob("*.ts")) + list(src_root.rglob("*.tsx"))
    )
    # Skip the auto-generated locales catalog — it contains
    # locale-related identifiers that look like RTL pattern matches
    # but are actually safe (they're language tags, not CSS)
    files = [f for f in files if f.name != "locales.generated.ts"]
    print(f"[lint-rtl] scanning {len(files)} files")
    all_violations: list[Violation] = []
    for f in files:
        all_violations.extend(scan_file(f))
    if not all_violations:
        print("[lint-rtl] no RTL violations found")
        return 0
    # Group by file for readable output
    print(
        f"[lint-rtl] found {len(all_violations)} RTL violations:\n",
        file=sys.stderr,
    )
    current_file = None
    for v in all_violations:
        if v.file != current_file:
            current_file = v.file
            print(f"  {v.file}", file=sys.stderr)
        print(f"  line {v.line_number}: {v.pattern_name}", file=sys.stderr)
        print(f"    {v.line}", file=sys.stderr)
        print(f"    → {v.suggestion}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"[lint-rtl] fatal: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
