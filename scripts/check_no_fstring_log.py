"""Pre-commit guard that forbids f-string interpolation in logger calls.

Logger calls should use deferred ``%``-style formatting (``logger.info("x=%s", x)``)
so the interpolation only runs when the log level is enabled. An f-string message
(``logger.info(f"x={x}")``) always interpolates eagerly, even if the record is
filtered out.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

LOG_METHODS = {"debug", "info", "warning", "warn", "error", "critical", "exception", "log"}


def _is_logger_call(node: ast.Call) -> bool:
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr in LOG_METHODS):
        return False
    target = func.value
    if isinstance(target, ast.Name):
        return target.id.lower().endswith("logger") or target.id.lower() == "log"
    if isinstance(target, ast.Attribute):
        return target.attr.lower().endswith("logger") or target.attr.lower() == "log"
    return False


def _message_arg(node: ast.Call) -> ast.expr | None:
    args = node.args
    if not args:
        return None
    # logger.log(level, msg, ...) takes the message as the second positional arg.
    if isinstance(node.func, ast.Attribute) and node.func.attr == "log":
        return args[1] if len(args) > 1 else None
    return args[0]


def _fstring_call_lines(path: Path) -> list[int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        print(f"{path}: could not parse Python file: {exc}", file=sys.stderr)
        return [exc.lineno or 1]

    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_logger_call(node):
            message = _message_arg(node)
            if isinstance(message, ast.JoinedStr):
                lines.append(node.lineno)
    return sorted(lines)


def main(argv: list[str]) -> int:
    failed = False
    for filename in argv:
        path = Path(filename)
        if path.suffix != ".py":
            continue
        for line in _fstring_call_lines(path):
            print(
                f"{path}:{line}: logger call uses an f-string message; "
                'use deferred %-style formatting instead (e.g. logger.info("x=%s", x))',
                file=sys.stderr,
            )
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
