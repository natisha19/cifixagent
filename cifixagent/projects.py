"""Dependency project adapters and lockfile-aware graph metadata.

Adapters deliberately produce plans; only the validator invokes package tools.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from .dependencies import load_requirements, requirement_name_from_line
from .models import PackageChange, ProjectMetadata
from .resolution import canonical_distribution_name

MAX_LOCK_BYTES = 5_000_000
MAX_RESOLVED_CHANGES = 50


@dataclass(slots=True)
class LockedPackage:
    name: str
    version: str
    dependencies: list[str]
    source: str | None


class ProjectAdapter(Protocol):
    name: str

    def metadata(self, root: Path) -> ProjectMetadata: ...

    def declared_dependencies(self, root: Path) -> set[str]: ...

    def install_command(self, root: Path, venv_python: str) -> list[str]: ...


def _requirement_names(items: object) -> set[str]:
    names: set[str] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, str):
            continue
        match = re.match(r"^\s*([A-Za-z0-9_.-]+)", item)
        if match:
            names.add(match.group(1))
    return names


def _digest(path: Path) -> str | None:
    if not path.exists():
        return None
    data = path.read_bytes()
    if len(data) > MAX_LOCK_BYTES:
        raise ValueError(f"{path.name} exceeds the {MAX_LOCK_BYTES} byte safety limit")
    return hashlib.sha256(data).hexdigest()


class PipAdapter:
    name = "pip"

    def metadata(self, root: Path) -> ProjectMetadata:
        return ProjectMetadata(
            adapter=self.name,
            manifest_path="requirements.txt",
            lockfile_path=None,
            declared_distributions=sorted(self.declared_dependencies(root)),
            index_configured=_pip_index_is_configured(root),
        )

    def declared_dependencies(self, root: Path) -> set[str]:
        path = root / "requirements.txt"
        if not path.exists():
            return set()
        requirements = load_requirements(path.read_text(encoding="utf-8"))
        return {
            name
            for line in requirements.text.splitlines()
            if (name := requirement_name_from_line(line)) is not None
        }

    def install_command(self, root: Path, venv_python: str) -> list[str]:
        return [venv_python, "-m", "pip", "install", "--requirement", "requirements.txt"]


class UvAdapter:
    name = "uv"

    def metadata(self, root: Path) -> ProjectMetadata:
        lock = root / "uv.lock"
        return ProjectMetadata(
            adapter=self.name,
            manifest_path="pyproject.toml",
            lockfile_path="uv.lock",
            declared_distributions=sorted(self.declared_dependencies(root)),
            index_configured=_uv_index_is_configured(root),
            lock_digest=_digest(lock),
        )

    def declared_dependencies(self, root: Path) -> set[str]:
        document = _read_toml(root / "pyproject.toml")
        project = document.get("project", {})
        if isinstance(project, dict):
            return _requirement_names(project.get("dependencies", []))
        return set()

    def install_command(self, root: Path, venv_python: str) -> list[str]:
        del root, venv_python
        # --locked makes the existing uv.lock authoritative and prevents hidden resolution.
        return ["uv", "sync", "--locked", "--no-install-project"]

    def locked_packages(self, root: Path) -> list[LockedPackage]:
        document = _read_toml(root / "uv.lock")
        packages = document.get("package", [])
        result: list[LockedPackage] = []
        for package in packages if isinstance(packages, list) else []:
            if not isinstance(package, dict) or not isinstance(package.get("name"), str):
                continue
            dependencies = []
            for dependency in package.get("dependencies", []):
                if isinstance(dependency, dict) and isinstance(dependency.get("name"), str):
                    dependencies.append(dependency["name"])
                elif isinstance(dependency, str):
                    dependencies.append(dependency)
            source = package.get("source")
            source_text = str(source) if source is not None else None
            result.append(
                LockedPackage(
                    str(package["name"]), str(package.get("version", "")), dependencies, source_text
                )
            )
        return result


def select_adapter(root: Path) -> ProjectAdapter:
    if (root / "pyproject.toml").exists() and (root / "uv.lock").exists():
        return UvAdapter()
    if (root / "requirements.txt").exists():
        return PipAdapter()
    raise FileNotFoundError("Expected either requirements.txt or pyproject.toml with uv.lock")


def lock_dependency_paths(packages: list[LockedPackage], target: str) -> list[list[str]]:
    """Return direct-to-target paths from lock metadata, capped to limit comment size."""
    by_name = {canonical_distribution_name(item.name): item for item in packages}
    target_key = canonical_distribution_name(target)
    children = {
        canonical_distribution_name(item.name): [canonical_distribution_name(dep) for dep in item.dependencies]
        for item in packages
    }
    referenced = {child for deps in children.values() for child in deps}
    roots = [name for name in children if name not in referenced] or list(children)
    paths: list[list[str]] = []
    for root in roots:
        stack = [(root, [root])]
        while stack and len(paths) < 20:
            node, path = stack.pop()
            if node == target_key:
                paths.append([by_name[item].name if item in by_name else item for item in path])
                continue
            for child in children.get(node, []):
                if child not in path:
                    stack.append((child, [*path, child]))
    return paths


def diff_locked_packages(before: list[LockedPackage], after: list[LockedPackage]) -> list[PackageChange]:
    before_map = {canonical_distribution_name(item.name): item for item in before}
    after_map = {canonical_distribution_name(item.name): item for item in after}
    changes: list[PackageChange] = []
    for key in sorted(set(before_map) | set(after_map)):
        old, new = before_map.get(key), after_map.get(key)
        if old is not None and new is not None and old.version == new.version:
            continue
        changes.append(
            PackageChange(
                new.name if new else old.name,
                old.version if old else None,
                new.version if new else None,
            )
        )
    if len(changes) > MAX_RESOLVED_CHANGES:
        raise ValueError(f"Lockfile update changes {len(changes)} packages; limit is {MAX_RESOLVED_CHANGES}")
    return changes


def _read_toml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    data = path.read_bytes()
    if len(data) > MAX_LOCK_BYTES:
        raise ValueError(f"{path.name} exceeds the {MAX_LOCK_BYTES} byte safety limit")
    parsed = tomllib.loads(data.decode("utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def _uv_index_is_configured(root: Path) -> bool:
    document = _read_toml(root / "pyproject.toml")
    tool = document.get("tool", {})
    uv = tool.get("uv", {}) if isinstance(tool, dict) else {}
    return isinstance(uv, dict) and bool(uv.get("index") or uv.get("default-index"))


def _pip_index_is_configured(root: Path) -> bool:
    path = root / "requirements.txt"
    if not path.exists():
        return False
    return any(
        line.strip().startswith(("--index-url", "--extra-index-url"))
        for line in path.read_text(encoding="utf-8").splitlines()
    )


def add_pep621_dependency(text: str, distribution: str) -> tuple[str, int, bool]:
    """Conservative edit for a PEP 621 dependency list.

    We only edit an existing list and leave dynamic/unsupported project metadata alone.
    """
    match = re.search(r"(?ms)^dependencies\s*=\s*\[(.*?)^\]", text)
    if match is None:
        return text, 0, False
    existing = _requirement_names(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)))
    if canonical_distribution_name(distribution) in {canonical_distribution_name(item) for item in existing}:
        return text, 0, False
    body = match.group(1)
    indentation = "    "
    replacement = body + ("" if body.endswith("\n") else "\n") + f'{indentation}"{distribution}",\n'
    updated = text[: match.start(1)] + replacement + text[match.end(1) :]
    return updated, updated[: match.start(1)].count("\n") + replacement.count("\n"), True
