from cifixagent.parsing import parse_failure, sanitize_log_excerpt


def test_parse_module_not_found():
    logs = """
Traceback (most recent call last):
  File "main.py", line 1, in <module>
    import yaml
ModuleNotFoundError: No module named 'yaml'
""".strip()

    failure = parse_failure(logs)

    assert failure is not None
    assert failure.kind == "ModuleNotFoundError"
    assert failure.module == "yaml"
    assert "ModuleNotFoundError" in failure.excerpt


def test_parse_import_error_no_module_named():
    logs = "ImportError: No module named 'sklearn'"
    failure = parse_failure(logs)
    assert failure is not None
    assert failure.module == "sklearn"


def test_parse_import_error_from_module():
    logs = "ImportError: cannot import name 'helper' from 'utils'"

    failure = parse_failure(logs)

    assert failure is not None
    assert failure.kind == "ImportError"
    assert failure.module == "utils"


def test_parse_malformed_logs_returns_none():
    assert parse_failure("") is None
    assert parse_failure("build failed without a supported import error") is None


def test_sanitize_log_excerpt_bounds_and_removes_ansi():
    logs = (
        "\n".join([f"line {index}" for index in range(100)])
        + "\n\x1b[31mModuleNotFoundError: No module named 'bs4'\x1b[0m"
    )

    excerpt = sanitize_log_excerpt(logs, max_lines=5, max_chars=120)

    assert "\x1b[31m" not in excerpt
    assert "ModuleNotFoundError" in excerpt
    assert len(excerpt) <= 135


def test_sanitize_very_small_max_chars():
    excerpt = sanitize_log_excerpt("ModuleNotFoundError: No module named 'x'", max_chars=5)
    assert len(excerpt) <= 5
