from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(slots=True)
class FailureObservation:
    kind: str
    module: str | None
    message: str
    excerpt: str
    line_number: int | None = None


@dataclass(slots=True)
class GraphNode:
    id: str
    kind: str
    label: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class GraphEdge:
    source: str
    target: str
    label: str


@dataclass(slots=True)
class BlastRadius:
    source_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)

    @property
    def source_count(self) -> int:
        return len(self.source_files)

    @property
    def test_count(self) -> int:
        return len(self.test_files)


@dataclass(slots=True)
class DependencyGraphReport:
    repo_root: str
    failure: FailureObservation
    module: str
    declared_distribution: str | None
    proposed_distribution: str | None
    confidence: float
    rationale: str
    is_stdlib: bool
    is_ambiguous: bool
    already_declared: bool
    blast_radius: BlastRadius
    graph_nodes: list[GraphNode] = field(default_factory=list)
    graph_edges: list[GraphEdge] = field(default_factory=list)


@dataclass(slots=True)
class ValidationResult:
    success: bool
    command: list[str]
    workspace: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass(slots=True)
class RequirementChange:
    path: str
    added_dependency: str
    line_number: int
    before: str
    after: str


@dataclass(slots=True)
class PackageChange:
    """A resolved package version change, normally derived from uv.lock."""

    name: str
    before: str | None
    after: str | None
    direct: bool = False


@dataclass(slots=True)
class ProjectMetadata:
    adapter: str
    manifest_path: str
    lockfile_path: str | None
    declared_distributions: list[str] = field(default_factory=list)
    index_configured: bool = False
    lock_digest: str | None = None


@dataclass(slots=True)
class FixProposal:
    failure: FailureObservation
    module: str
    proposed_distribution: str | None
    confidence: float
    explanation: str
    changed_files: list[str]
    graph: DependencyGraphReport
    validation_result: ValidationResult | None = None
    safe_to_apply: bool = False
    approval_command: str = "/ci-janitor approve"
    proposal_marker: str = "<!-- ci-janitor:proposal -->"
    refusal_reason: str | None = None
    requirement_change: RequirementChange | None = None
    additional_changes: list[RequirementChange] = field(default_factory=list)
    project: ProjectMetadata | None = None
    root_cause_package: str | None = None
    dependency_paths: list[list[str]] = field(default_factory=list)
    resolved_changes: list[PackageChange] = field(default_factory=list)
    analyzed_head_sha: str | None = None
    workflow_run_id: str | None = None

    def apply_to_workspace(self, workspace_root: Path) -> None:
        changes = ([self.requirement_change] if self.requirement_change is not None else []) + self.additional_changes
        for change in changes:
            target = workspace_root / change.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.after, encoding="utf-8")

    def to_dict(self) -> dict[str, object]:
        return {
            "failure": {
                "kind": self.failure.kind,
                "module": self.failure.module,
                "message": self.failure.message,
                "excerpt": self.failure.excerpt,
                "line_number": self.failure.line_number,
            },
            "module": self.module,
            "proposed_distribution": self.proposed_distribution,
            "confidence": self.confidence,
            "explanation": self.explanation,
            "changed_files": self.changed_files,
            "graph": {
                "repo_root": self.graph.repo_root,
                "declared_distribution": self.graph.declared_distribution,
                "proposed_distribution": self.graph.proposed_distribution,
                "confidence": self.graph.confidence,
                "rationale": self.graph.rationale,
                "is_stdlib": self.graph.is_stdlib,
                "is_ambiguous": self.graph.is_ambiguous,
                "already_declared": self.graph.already_declared,
                "blast_radius": {
                    "source_files": self.graph.blast_radius.source_files,
                    "test_files": self.graph.blast_radius.test_files,
                },
                "graph_nodes": [asdict(node) for node in self.graph.graph_nodes],
                "graph_edges": [asdict(edge) for edge in self.graph.graph_edges],
            },
            "safe_to_apply": self.safe_to_apply,
            "approval_command": self.approval_command,
            "refusal_reason": self.refusal_reason,
            "requirement_change": None
            if self.requirement_change is None
            else {
                "path": self.requirement_change.path,
                "added_dependency": self.requirement_change.added_dependency,
                "line_number": self.requirement_change.line_number,
            },
            "additional_changes": [
                {"path": change.path, "added_dependency": change.added_dependency}
                for change in self.additional_changes
            ],
            "project": None if self.project is None else asdict(self.project),
            "root_cause_package": self.root_cause_package,
            "dependency_paths": self.dependency_paths,
            "resolved_changes": [asdict(change) for change in self.resolved_changes],
            "analyzed_head_sha": self.analyzed_head_sha,
            "workflow_run_id": self.workflow_run_id,
            "validation_result": None
            if self.validation_result is None
            else {
                "success": self.validation_result.success,
                "command": self.validation_result.command,
                "workspace": self.validation_result.workspace,
                "exit_code": self.validation_result.exit_code,
                "stdout": self.validation_result.stdout,
                "stderr": self.validation_result.stderr,
                "timed_out": self.validation_result.timed_out,
            },
        }

    def to_text(self) -> str:
        lines = [
            f"Failure: {self.failure.kind} for {self.module}",
            f"Proposed distribution: {self.proposed_distribution or 'none'}",
            f"Confidence: {self.confidence:.2f}",
            f"Why: {self.explanation}",
            f"Already declared: {'yes' if self.graph.already_declared else 'no'}",
            f"Stdlib: {'yes' if self.graph.is_stdlib else 'no'}",
            (
                f"Blast radius: {self.graph.blast_radius.source_count} source files, "
                f"{self.graph.blast_radius.test_count} test files"
            ),
            f"Safe to apply: {'yes' if self.safe_to_apply else 'no'}",
        ]
        if self.project is not None:
            lines.append(f"Project adapter: {self.project.adapter}")
        if self.resolved_changes:
            lines.append(f"Resolved changes: {len(self.resolved_changes)} package(s)")
        if self.refusal_reason:
            lines.append(f"Refusal: {self.refusal_reason}")
        if self.validation_result is not None:
            state = "passed" if self.validation_result.success else "failed"
            lines.append(f"Validation: {state} ({' '.join(self.validation_result.command)})")
        return "\n".join(lines)

    def render_comment(self, run_url: str) -> str:
        validation_line = "Validation: not run yet"
        if self.validation_result is not None:
            state = "passed" if self.validation_result.success else "failed"
            validation_line = f"Validation: {state} using {' '.join(self.validation_result.command)}"

        radius = self.graph.blast_radius
        changed = ", ".join(self.changed_files) if self.changed_files else "none"

        body = [
            self.proposal_marker,
            "## CI Janitor analysis",
            "",
            f"**Failure:** `{self.failure.kind}` for `{self.module}`",
            (
                f"**Mapping:** `{self.module}` -> "
                f"`{self.proposed_distribution or 'unresolved'}` "
                f"(confidence {self.confidence:.2f})"
            ),
            f"**Why:** {self.explanation}",
            f"**Minimal change:** {changed}",
            (
                f"**Graph impact / blast radius:** {radius.source_count} source files, "
                f"{radius.test_count} test files "
                "(direct imports only; no transitive claims without lockfile metadata)"
            ),
            f"**Validation:** {validation_line.removeprefix('Validation: ')}",
            f"**Approval:** reply with exactly `{self.approval_command}`",
            f"**Workflow run:** {run_url}",
        ]
        if self.refusal_reason:
            body.append(f"Refusal: {self.refusal_reason}")
        if self.requirement_change is not None:
            body.append("")
            body.append("<details><summary>Requirement patch</summary>")
            body.append("")
            body.append("```text")
            body.append(self.requirement_change.after)
            body.append("```")
            body.append("</details>")
        body.append("")
        body.append("<details><summary>Log excerpt</summary>")
        body.append("")
        body.append("```text")
        body.append(self.failure.excerpt)
        body.append("```")
        body.append("</details>")
        return "\n".join(body)
