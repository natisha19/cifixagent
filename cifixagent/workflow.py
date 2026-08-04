"""GitHub Actions entrypoints for CI Janitor propose/apply workflows.

Trusted agent code must be checked out from the default branch (or a pinned
trusted commit). The PR tree may be present as editable data, but this module
never installs or executes untrusted PR Python packages with write credentials.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from .github import GitHubClient
from .service import APPROVAL_COMMAND, PROPOSAL_MARKER, CIJanitorService, is_exact_approval_comment
from .validation import CopyWorkspaceValidator

LOGGER = logging.getLogger("cifixagent.workflow")


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Required environment variable {name} is missing.")
    return value


def _client() -> GitHubClient:
    return GitHubClient(token=_env("GITHUB_TOKEN"), repo=_env("REPO"))


def _service(repo_root: Path) -> CIJanitorService:
    return CIJanitorService(repo_root, CopyWorkspaceValidator())


def _find_pr_number(client: GitHubClient, run_id: str) -> int:
    if os.environ.get("PR_NUMBER"):
        return int(os.environ["PR_NUMBER"])
    run = client.get_workflow_run(run_id)
    pull_requests = run.get("pull_requests") or []
    if not pull_requests:
        raise RuntimeError("No pull request associated with this workflow run.")
    return int(pull_requests[0]["number"])


def propose_main(argv: list[str] | None = None) -> int:
    del argv
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    client = _client()
    run_id = _env("RUN_ID")
    repo_root = Path(_env("REPO_ROOT", ".")).resolve()
    service = _service(repo_root)

    logs = client.get_workflow_logs(run_id)
    proposal = service.propose(logs)
    pr_number = _find_pr_number(client, run_id)
    run = client.get_workflow_run(run_id)
    run_url = str(run.get("html_url") or f"https://github.com/{client.repo}/actions/runs/{run_id}")

    # Validate only when we have a concrete high-confidence patch candidate.
    if proposal.requirement_change is not None and proposal.proposed_distribution is not None:
        command = os.environ.get("CI_JANITOR_VALIDATE_CMD", "python -m pytest").split()
        service.validate(proposal, command, allow_network=False)
        if not proposal.safe_to_apply:
            proposal.refusal_reason = (
                proposal.refusal_reason
                or "Validation failed; publishing diagnosis without an apply recommendation."
            )

    comment = proposal.render_comment(run_url)
    client.ensure_comment(pr_number, comment, PROPOSAL_MARKER)
    LOGGER.info("Posted proposal comment on PR #%s", pr_number)
    return 0


def apply_main(argv: list[str] | None = None) -> int:
    del argv
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    client = _client()
    comment_body = _env("COMMENT_BODY")
    commenter = _env("COMMENTER_LOGIN")
    pr_number = int(_env("PR_NUMBER"))
    branch = _env("PR_BRANCH")
    bot_branch = _env("BOT_BRANCH")
    repo_root = Path(_env("REPO_ROOT", ".")).resolve()
    run_id = os.environ.get("RUN_ID")

    if not is_exact_approval_comment(comment_body):
        LOGGER.info("Ignoring non-exact approval comment")
        return 0

    pull_request = client.get_pull_request(pr_number)
    decision = _service(repo_root).authorize_comment(
        body=comment_body,
        commenter_login=commenter,
        pull_request=pull_request,
        permission_lookup=client.get_collaborator_permission,
    )
    if not decision.approved:
        client.ensure_comment(
            pr_number,
            f"{PROPOSAL_MARKER}\nCI Janitor refused apply: {decision.reason}",
            PROPOSAL_MARKER,
        )
        LOGGER.warning("Apply refused: %s", decision.reason)
        return 1

    if not client.base_and_head_same_repo(pull_request):
        client.ensure_comment(
            pr_number,
            f"{PROPOSAL_MARKER}\nCI Janitor refused apply: fork PRs are out of MVP scope.",
            PROPOSAL_MARKER,
        )
        return 1

    if not run_id:
        # Discover latest failed CI run for the PR head SHA.
        head_sha = str(pull_request.get("head", {}).get("sha", ""))
        runs = client.list_workflow_runs().get("workflow_runs", [])
        chosen = None
        for item in runs:
            if item.get("head_sha") != head_sha:
                continue
            name = str(item.get("name") or "").lower()
            if "ci" in name and item.get("conclusion") == "failure":
                chosen = item
                break
        if chosen is None:
            for item in runs:
                if item.get("head_sha") == head_sha and item.get("conclusion") == "failure":
                    chosen = item
                    break
        if chosen is None:
            raise RuntimeError("Could not find a failed CI run for this PR.")
        run_id = str(chosen["id"])

    service = _service(repo_root)
    logs = client.get_workflow_logs(run_id)
    proposal = service.propose(logs)
    if proposal.requirement_change is None:
        client.ensure_comment(
            pr_number,
            f"{PROPOSAL_MARKER}\nCI Janitor refused apply: {proposal.refusal_reason}",
            PROPOSAL_MARKER,
        )
        return 1

    command = os.environ.get("CI_JANITOR_VALIDATE_CMD", "python -m pytest").split()
    result = service.validate(proposal, command, allow_network=False)
    if not proposal.safe_to_apply or not result.success:
        client.ensure_comment(
            pr_number,
            (
                f"{PROPOSAL_MARKER}\n"
                "CI Janitor refused apply because validation failed.\n"
                f"Reason: {proposal.refusal_reason or 'validation unsuccessful'}\n"
                f"Reply again with `{APPROVAL_COMMAND}` after CI is fixed manually if needed."
            ),
            PROPOSAL_MARKER,
        )
        return 2

    service.apply_with_git(proposal, bot_branch)
    run = client.get_workflow_run(run_id)
    run_url = str(run.get("html_url") or f"https://github.com/{client.repo}/actions/runs/{run_id}")
    remediation = client.create_pull_request(
        head=bot_branch,
        base=branch,
        title=f"ci-janitor: remediate {proposal.proposed_distribution}",
        body=(
            "Automated, validated dependency remediation.\n\n"
            f"Source CI run: {run_url}\n\n"
            f"Dependency path(s): {proposal.dependency_paths or 'not available'}"
        ),
    )
    client.ensure_comment(
        pr_number,
        (
            f"{PROPOSAL_MARKER}\n"
            f"CI Janitor opened remediation PR #{remediation.get('number', '?')} for "
            f"`{proposal.proposed_distribution}` after validation.\n"
            f"Workflow run: {run_url}"
        ),
        PROPOSAL_MARKER,
    )
    LOGGER.info("Applied fix to branch %s", branch)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python -m cifixagent.workflow [propose|apply]", file=sys.stderr)
        return 2
    command = argv[0]
    if command == "propose":
        return propose_main(argv[1:])
    if command == "apply":
        return apply_main(argv[1:])
    print(f"unknown workflow command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
