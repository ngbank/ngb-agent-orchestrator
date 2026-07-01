"""Tests for the check_no_fstring_log pre-commit guard."""

from pathlib import Path

from scripts.check_no_fstring_log import main


def _write(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "sample.py"
    path.write_text(source)
    return path


def test_rejects_fstring_logger_message(tmp_path):
    path = _write(
        tmp_path,
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "value = 1\n"
        'logger.info(f"value={value}")\n',
    )

    assert main([str(path)]) == 1


def test_rejects_fstring_via_log_method(tmp_path):
    path = _write(
        tmp_path,
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "value = 1\n"
        'logger.log(logging.INFO, f"value={value}")\n',
    )

    assert main([str(path)]) == 1


def test_allows_percent_style_logger_message(tmp_path):
    path = _write(
        tmp_path,
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "value = 1\n"
        'logger.info("value=%s", value)\n',
    )

    assert main([str(path)]) == 0


def test_ignores_fstrings_unrelated_to_logging(tmp_path):
    path = _write(
        tmp_path,
        "value = 1\n" 'message = f"value={value}"\n' "print(message)\n",
    )

    assert main([str(path)]) == 0
