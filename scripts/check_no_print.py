"""Pre-commit guard that forbids production ``print()`` calls."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ALLOWED_TOP_LEVEL_DIRS = {"scripts", "tests"}


def _is_allowed(path: Path) -> bool:
    parts = path.parts
    return bool(parts) and parts[0] in ALLOWED_TOP_LEVEL_DIRS


def _print_call_lines(path: Path) -> list[int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        print(f"{path}: could not parse Python file: {exc}", file=sys.stderr)
        return [exc.lineno or 1]

    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ):
            lines.append(node.lineno)
    return sorted(lines)


def main(argv: list[str]) -> int:
    failed = False
    for filename in argv:
        path = Path(filename)
        if path.suffix != ".py" or _is_allowed(path):
            continue
        for line in _print_call_lines(path):
            print(
                f"{path}:{line}: print() is not allowed outside scripts/ and tests/; "
                "use logging instead",
                file=sys.stderr,
            )
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
