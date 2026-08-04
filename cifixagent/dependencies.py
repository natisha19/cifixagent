from __future__ import annotations

import re
from dataclasses import dataclass

from .resolution import canonical_distribution_name

COMMENT_OR_DIRECTIVE_PREFIXES = ("#", "-r ", "--", "-e ", "-c ", "http://", "https://", "git+")


@dataclass(slots=True)
class RequirementsFile:
    text: str

    def normalized_names(self) -> set[str]:
        names: set[str] = set()
        for line in self.text.splitlines():
            name = requirement_name_from_line(line)
            if name:
                names.add(canonical_distribution_name(name))
        return names

    def contains(self, distribution: str) -> bool:
        target = canonical_distribution_name(distribution)
        return target in self.normalized_names()

    def add(self, distribution: str) -> tuple[str, int, bool]:
        lines = self.text.splitlines()
        target = canonical_distribution_name(distribution)

        for line in lines:
            name = requirement_name_from_line(line)
            if name and canonical_distribution_name(name) == target:
                return self.text, len(lines), False

        new_lines = list(lines)
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(distribution)
        updated = "\n".join(new_lines)
        if not updated.endswith("\n"):
            updated += "\n"
        return updated, len(new_lines), True


def requirement_name_from_line(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith(COMMENT_OR_DIRECTIVE_PREFIXES):
        return None

    if stripped.startswith("["):
        return None

    match = re.match(r"^([A-Za-z0-9_.-]+)", stripped)
    if not match:
        return None

    name = match.group(1)
    if name.endswith((".whl", ".tar.gz", ".zip")):
        return None
    return name


def load_requirements(path_text: str) -> RequirementsFile:
    return RequirementsFile(path_text)
