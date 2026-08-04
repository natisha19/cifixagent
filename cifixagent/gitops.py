from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger("cifixagent.gitops")


class GitError(RuntimeError):
    pass


@dataclass(slots=True)
class GitRepository:
    root: Path

    def _run(
        self, args: list[str], *, capture_output: bool = False, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        LOGGER.debug("git %s", " ".join(args))
        completed = subprocess.run(
            ["git", *args],
            cwd=self.root,
            check=False,
            capture_output=capture_output,
            text=True,
        )
        if check and completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise GitError(f"git {' '.join(args)} failed: {detail}")
        return completed

    def status_porcelain(self) -> str:
        return self._run(["status", "--porcelain"], capture_output=True).stdout.strip()

    def set_identity(self) -> None:
        self._run(["config", "user.name", "ci-janitor-bot"])
        self._run(["config", "user.email", "ci-janitor@users.noreply.github.com"])

    def checkout(self, ref: str) -> None:
        self._run(["checkout", ref])

    def create_branch(self, branch: str) -> None:
        self._run(["checkout", "-b", branch])

    def stage(self, paths: list[str]) -> None:
        self._run(["add", "--", *paths])

    def staged_names(self) -> list[str]:
        output = self._run(["diff", "--cached", "--name-only"], capture_output=True).stdout
        return [line.strip() for line in output.splitlines() if line.strip()]

    def staged_diff(self, path: str) -> str:
        return self._run(["diff", "--cached", "--", path], capture_output=True).stdout

    def working_tree_file(self, path: str) -> str:
        return (self.root / path).read_text(encoding="utf-8")

    def commit(self, message: str) -> None:
        self._run(["commit", "-m", message])

    def push(self, ref: str) -> None:
        # Push only the named same-repo PR branch after successful validation.
        self._run(["push", "origin", f"HEAD:{ref}"])

    def confirm_staged_matches_proposal(self, expected: dict[str, str]) -> None:
        staged = self.staged_names()
        if sorted(staged) != sorted(expected):
            raise GitError(
                f"Refusing to commit unexpected staged files: {staged!r}; expected {sorted(expected)!r}"
            )
        for path, after in expected.items():
            if self.working_tree_file(path) != after:
                raise GitError(f"Working tree file {path} does not match the approved proposal.")
