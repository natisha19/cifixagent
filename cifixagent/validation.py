from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .models import FixProposal, RequirementChange, ValidationResult
from .projects import UvAdapter, diff_locked_packages, select_adapter

LOGGER = logging.getLogger("cifixagent.validation")


@dataclass(slots=True)
class ValidationSettings:
    command: list[str]
    timeout_seconds: int = 120
    max_output_chars: int = 8000
    allow_network: bool = False
    install_dependencies: bool = True


class Validator:
    def validate(
        self, repo_root: Path, proposal: FixProposal, settings: ValidationSettings
    ) -> ValidationResult:
        raise NotImplementedError


class CopyWorkspaceValidator(Validator):
    """Apply a proposal in an isolated copy and run a configured command.

    Network access is opt-in. Unit tests should inject a fake validator or
    run commands that do not contact PyPI.
    """

    def validate(
        self, repo_root: Path, proposal: FixProposal, settings: ValidationSettings
    ) -> ValidationResult:
        with tempfile.TemporaryDirectory(prefix="cifixagent-validate-") as temp_dir:
            temp_root = Path(temp_dir) / "workspace"
            shutil.copytree(
                repo_root,
                temp_root,
                ignore=shutil.ignore_patterns(
                    ".git",
                    "__pycache__",
                    ".pytest_cache",
                    ".venv",
                    "venv",
                    "env",
                    ".mypy_cache",
                    ".ruff_cache",
                    "pytest-cache-files-*",
                ),
            )
            proposal.apply_to_workspace(temp_root)
            env = _untrusted_environment()
            env["CIFIXAGENT_ALLOW_NETWORK"] = "1" if settings.allow_network else "0"
            command = settings.command

            try:
                if settings.install_dependencies:
                    venv_python = _venv_python(temp_root)
                    _run_checked(
                        [sys.executable, "-m", "venv", ".cifixagent-venv"],
                        temp_root,
                        env,
                        settings.timeout_seconds,
                    )
                    if not settings.allow_network:
                        return ValidationResult(
                            False,
                            settings.command,
                            str(temp_root),
                            None,
                            "",
                            "Dependency installation requires explicit --allow-network.",
                        )
                    adapter = select_adapter(temp_root)
                    if not adapter.metadata(temp_root).index_configured:
                        return ValidationResult(
                            False,
                            settings.command,
                            str(temp_root),
                            None,
                            "",
                            "No repository-configured package index; refusing index fallback.",
                        )
                    if isinstance(adapter, UvAdapter):
                        old_lock = (temp_root / "uv.lock").read_text(encoding="utf-8")
                        old_packages = adapter.locked_packages(temp_root)
                        _run_checked(["uv", "lock"], temp_root, env, settings.timeout_seconds)
                        new_lock = (temp_root / "uv.lock").read_text(encoding="utf-8")
                        proposal.resolved_changes = diff_locked_packages(
                            old_packages, adapter.locked_packages(temp_root)
                        )
                        if old_lock != new_lock:
                            proposal.additional_changes = [
                                RequirementChange(
                                    path="uv.lock",
                                    added_dependency=proposal.proposed_distribution or "",
                                    line_number=0,
                                    before=old_lock,
                                    after=new_lock,
                                )
                            ]
                            if "uv.lock" not in proposal.changed_files:
                                proposal.changed_files.append("uv.lock")
                    install = adapter.install_command(temp_root, str(venv_python))
                    _run_checked(install, temp_root, env, settings.timeout_seconds)
                    command = _with_venv_python(settings.command, venv_python)
                completed = subprocess.run(
                    command,
                    cwd=temp_root,
                    capture_output=True,
                    text=True,
                    timeout=settings.timeout_seconds,
                    env=env,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                stdout = _bound_text(exc.stdout or "", settings.max_output_chars)
                stderr = _bound_text(exc.stderr or "", settings.max_output_chars)
                return ValidationResult(
                    success=False,
                    command=command if 'command' in locals() else settings.command,
                    workspace=str(temp_root),
                    exit_code=None,
                    stdout=stdout,
                    stderr=stderr or "Validation timed out.",
                    timed_out=True,
                )
            except RuntimeError as exc:
                return ValidationResult(
                    success=False,
                    command=settings.command,
                    workspace=str(temp_root),
                    exit_code=None,
                    stdout="",
                    stderr=str(exc),
                    timed_out=False,
                )

            stdout = _bound_text(completed.stdout, settings.max_output_chars)
            stderr = _bound_text(completed.stderr, settings.max_output_chars)
            return ValidationResult(
                success=completed.returncode == 0,
                command=command,
                workspace=str(temp_root),
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                timed_out=False,
            )


def _bound_text(text: str | bytes | None, max_chars: int) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... (truncated)"


def _untrusted_environment() -> dict[str, str]:
    """Never pass GitHub credentials into a process that can execute PR code."""
    blocked = {"GITHUB_TOKEN", "GH_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL"}
    return {key: value for key, value in os.environ.items() if key not in blocked}


def _venv_python(root: Path) -> Path:
    return root / ".cifixagent-venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _with_venv_python(command: list[str], python: Path) -> list[str]:
    if command and command[0] in {"python", "python3", sys.executable}:
        return [str(python), *command[1:]]
    return command


def _run_checked(command: list[str], cwd: Path, env: dict[str, str], timeout: int) -> None:
    completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    if completed.returncode != 0:
        message = _bound_text(completed.stderr or completed.stdout, 2000)
        raise RuntimeError(f"Dependency setup failed: {message}")
