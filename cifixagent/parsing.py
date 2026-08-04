from __future__ import annotations

import re

from .models import FailureObservation

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def sanitize_log_excerpt(text: str, max_lines: int = 30, max_chars: int = 1800) -> str:
    cleaned = ANSI_RE.sub("", text)
    lines = cleaned.splitlines()

    match_index = next(
        (
            index
            for index, line in enumerate(lines)
            if "ModuleNotFoundError" in line or "ImportError" in line
        ),
        None,
    )

    if match_index is None:
        snippet = lines[:max_lines]
    else:
        start = max(0, match_index - 10)
        end = min(len(lines), match_index + 11)
        snippet = lines[start:end]

    excerpt = "\n".join(line.rstrip() for line in snippet).strip()
    if len(excerpt) > max_chars:
        suffix = "\n... (truncated)"
        if max_chars <= len(suffix):
            return excerpt[:max_chars]
        excerpt = excerpt[: max_chars - len(suffix)] + suffix
    return excerpt


MODULE_NOT_FOUND_PATTERNS = (
    re.compile(r"ModuleNotFoundError:\s+No module named ['\"]([^'\"]+)['\"]"),
    re.compile(r"ImportError:\s+No module named ['\"]([^'\"]+)['\"]"),
)

RESOLVER_PATTERNS = (
    re.compile(r"(?:No solution found|ResolutionImpossible).*?(?:for|package)\s+['\"]?([A-Za-z0-9_.-]+)", re.I | re.S),
    re.compile(r"Could not find a version that satisfies the requirement\s+([A-Za-z0-9_.-]+)", re.I),
)

IMPORT_FROM_PATTERN = re.compile(
    r"ImportError:\s+cannot import name ['\"]?([^'\"]+)['\"]?\s+from ['\"]([^'\"]+)['\"]"
)


def parse_failure(logs: str) -> FailureObservation | None:
    if not logs.strip():
        return None

    excerpt = sanitize_log_excerpt(logs)

    for pattern in MODULE_NOT_FOUND_PATTERNS:
        match = pattern.search(logs)
        if match:
            module = match.group(1).strip()
            return FailureObservation(
                kind="ModuleNotFoundError",
                module=module,
                message=match.group(0).strip(),
                excerpt=excerpt,
            )

    match = IMPORT_FROM_PATTERN.search(logs)
    if match:
        symbol = match.group(1).strip()
        module = match.group(2).strip()
        return FailureObservation(
            kind="ImportError",
            module=module,
            message=f"cannot import name {symbol} from {module}",
            excerpt=excerpt,
        )

    for pattern in RESOLVER_PATTERNS:
        match = pattern.search(logs)
        if match:
            package = match.group(1).strip()
            return FailureObservation(
                kind="ResolverError",
                module=package,
                message=match.group(0).strip()[:500],
                excerpt=excerpt,
            )

    return None
