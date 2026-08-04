from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .dependencies import RequirementsFile
from .gitops import GitError, GitRepository
from .graph import build_dependency_graph
from .models import FailureObservation, FixProposal, RequirementChange, ValidationResult
from .parsing import parse_failure
from .projects import UvAdapter, add_pep621_dependency, lock_dependency_paths, select_adapter
from .validation import ValidationSettings, Validator

LOGGER = logging.getLogger("cifixagent.service")

HIGH_CONFIDENCE_THRESHOLD = 0.9
PROPOSAL_MARKER = "<!-- ci-janitor:proposal -->"
APPROVAL_COMMAND = "/ci-janitor approve"


@dataclass(slots=True)
class ApprovalDecision:
    approved: bool
    reason: str


def is_exact_approval_comment(body: str) -> bool:
    return body.strip() == APPROVAL_COMMAND


class CIJanitorService:
    def __init__(self, repo_root: Path, validator: Validator):
        self.repo_root = repo_root
        self.validator = validator

    def parse_and_graph(self, logs: str):
        failure = parse_failure(logs)
        if failure is None:
            return None, None
        graph, _ = build_dependency_graph(self.repo_root, failure)
        return failure, graph

    def propose(self, logs: str) -> FixProposal:
        failure = parse_failure(logs)
        if failure is None:
            graph = self._empty_graph(logs)
            return FixProposal(
                failure=FailureObservation(
                    kind="unclassified",
                    module=None,
                    message="No dependency failure found.",
                    excerpt=graph.failure.excerpt,
                ),
                module="",
                proposed_distribution=None,
                confidence=0.0,
                explanation="The log does not contain a ModuleNotFoundError or supported ImportError.",
                changed_files=[],
                graph=graph,
                safe_to_apply=False,
                refusal_reason="No supported dependency failure was detected.",
            )

        graph, decision = build_dependency_graph(self.repo_root, failure)
        adapter = select_adapter(self.repo_root)
        metadata = adapter.metadata(self.repo_root)
        manifest_path = self.repo_root / metadata.manifest_path
        manifest_text = manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else ""
        requirements = RequirementsFile(manifest_text)

        if decision.is_stdlib:
            return FixProposal(
                failure=failure,
                module=graph.module,
                proposed_distribution=None,
                confidence=decision.confidence,
                explanation=decision.rationale,
                changed_files=[],
                graph=graph,
                safe_to_apply=False,
                refusal_reason="The failing import resolves to the Python standard library.",
                project=metadata,
            )

        if decision.proposed_distribution is None or decision.is_ambiguous:
            return FixProposal(
                failure=failure,
                module=graph.module,
                proposed_distribution=None,
                confidence=decision.confidence,
                explanation=decision.rationale,
                changed_files=[],
                graph=graph,
                safe_to_apply=False,
                refusal_reason="No high-confidence import-to-distribution mapping is available.",
                project=metadata,
            )

        is_declared = decision.already_declared or decision.proposed_distribution.casefold() in {
            item.casefold() for item in metadata.declared_distributions
        }
        if is_declared:
            return FixProposal(
                failure=failure,
                module=graph.module,
                proposed_distribution=decision.proposed_distribution,
                confidence=decision.confidence,
                explanation="The distribution already appears in requirements.txt.",
                changed_files=[],
                graph=graph,
                safe_to_apply=False,
                refusal_reason="The dependency is already declared.",
                project=metadata,
            )

        if decision.confidence < HIGH_CONFIDENCE_THRESHOLD:
            return FixProposal(
                failure=failure,
                module=graph.module,
                proposed_distribution=decision.proposed_distribution,
                confidence=decision.confidence,
                explanation=decision.rationale,
                changed_files=[],
                graph=graph,
                safe_to_apply=False,
                refusal_reason="Confidence is below the safe auto-apply threshold.",
                project=metadata,
            )

        if failure.kind == "ImportError" and failure.message.startswith("cannot import name"):
            return FixProposal(
                failure=failure,
                module=graph.module,
                proposed_distribution=None,
                confidence=0.2,
                explanation=(
                    "An import-name error usually indicates an API/version incompatibility; "
                    "resolver evidence is required."
                ),
                changed_files=[],
                graph=graph,
                project=metadata,
                refusal_reason="No resolver-backed version repair is available.",
            )

        if isinstance(adapter, UvAdapter):
            updated_text, line_number, changed = add_pep621_dependency(manifest_text, decision.proposed_distribution)
        else:
            updated_text, line_number, changed = requirements.add(decision.proposed_distribution)
        if not changed:
            return FixProposal(
                failure=failure, module=graph.module, proposed_distribution=decision.proposed_distribution,
                confidence=decision.confidence, explanation="The project manifest could not be edited conservatively.",
                changed_files=[], graph=graph, project=metadata,
                refusal_reason="No safe minimal manifest edit is available.",
            )
        requirement_change = RequirementChange(
            path=metadata.manifest_path,
            added_dependency=decision.proposed_distribution,
            line_number=line_number,
            before=manifest_text,
            after=updated_text,
        )

        paths: list[list[str]] = []
        if isinstance(adapter, UvAdapter):
            paths = lock_dependency_paths(adapter.locked_packages(self.repo_root), decision.proposed_distribution)

        return FixProposal(
            failure=failure,
            module=graph.module,
            proposed_distribution=decision.proposed_distribution,
            confidence=decision.confidence,
            explanation=decision.rationale,
            changed_files=[metadata.manifest_path] if changed else [],
            graph=graph,
            safe_to_apply=False,
            requirement_change=requirement_change,
            project=metadata,
            root_cause_package=decision.proposed_distribution,
            dependency_paths=paths,
        )

    def validate(
        self,
        proposal: FixProposal,
        command: list[str],
        timeout_seconds: int = 120,
        *,
        allow_network: bool = False,
    ) -> ValidationResult:
        settings = ValidationSettings(
            command=command,
            timeout_seconds=timeout_seconds,
            allow_network=allow_network,
        )
        result = self.validator.validate(self.repo_root, proposal, settings)
        proposal.validation_result = result
        proposal.safe_to_apply = (
            result.success
            and proposal.proposed_distribution is not None
            and proposal.requirement_change is not None
            and proposal.confidence >= HIGH_CONFIDENCE_THRESHOLD
            and not proposal.graph.is_stdlib
            and not proposal.graph.is_ambiguous
            and not proposal.graph.already_declared
            and proposal.refusal_reason is None
            and proposal.project is not None
        )
        if not result.success:
            proposal.safe_to_apply = False
            LOGGER.info("Validation failed; proposal marked unsafe")
        return result

    def apply_locally(self, proposal: FixProposal) -> None:
        if not proposal.safe_to_apply or proposal.requirement_change is None:
            raise RuntimeError("Refusing to apply an unsafe proposal.")
        proposal.apply_to_workspace(self.repo_root)
        LOGGER.info("Applied proposal to %s", proposal.requirement_change.path)

    def apply_with_git(self, proposal: FixProposal, branch: str) -> None:
        if not proposal.safe_to_apply or proposal.requirement_change is None:
            raise RuntimeError("Refusing to apply an unsafe proposal.")

        repo = GitRepository(self.repo_root)
        change = proposal.requirement_change
        repo.create_branch(branch)
        proposal.apply_to_workspace(self.repo_root)
        repo.set_identity()
        expected = {change.path: change.after, **{item.path: item.after for item in proposal.additional_changes}}
        repo.stage(list(expected))
        try:
            repo.confirm_staged_matches_proposal(expected)
        except GitError:
            LOGGER.exception("Staged diff did not match proposal")
            raise
        if not repo.status_porcelain():
            raise RuntimeError("No changes detected after applying proposal.")
        repo.commit(f"ci-janitor: add missing dependency {change.added_dependency}")
        repo.push(branch)

    def authorize_comment(
        self,
        *,
        body: str,
        commenter_login: str,
        pull_request: dict[str, object],
        permission_lookup: Callable[[str], str],
    ) -> ApprovalDecision:
        if not is_exact_approval_comment(body):
            return ApprovalDecision(
                False, f"Approval comment must match {APPROVAL_COMMAND} exactly."
            )

        head = pull_request.get("head", {})
        base = pull_request.get("base", {})
        head_repo = (head.get("repo") or {}) if isinstance(head, dict) else {}
        base_repo = (base.get("repo") or {}) if isinstance(base, dict) else {}
        if str(head_repo.get("full_name", "")) != str(base_repo.get("full_name", "")):
            return ApprovalDecision(
                False, "Fork pull requests are not eligible for automatic apply in MVP."
            )

        permission = permission_lookup(commenter_login)
        if permission not in {"write", "maintain", "admin"}:
            return ApprovalDecision(False, "Commenter must have write or higher permission.")

        return ApprovalDecision(True, "Approved")

    def _empty_graph(self, logs: str):
        from .models import BlastRadius, DependencyGraphReport
        from .parsing import sanitize_log_excerpt

        failure = FailureObservation(
            kind="unclassified",
            module=None,
            message="No dependency failure found.",
            excerpt=sanitize_log_excerpt(logs),
        )
        return DependencyGraphReport(
            repo_root=str(self.repo_root),
            failure=failure,
            module="",
            declared_distribution=None,
            proposed_distribution=None,
            confidence=0.0,
            rationale="No supported dependency failure was detected.",
            is_stdlib=False,
            is_ambiguous=True,
            already_declared=False,
            blast_radius=BlastRadius(),
            graph_nodes=[],
            graph_edges=[],
        )
