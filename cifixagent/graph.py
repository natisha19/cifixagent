from __future__ import annotations

import ast
import json
from dataclasses import asdict
from pathlib import Path

from .dependencies import load_requirements, requirement_name_from_line
from .models import BlastRadius, DependencyGraphReport, FailureObservation, GraphEdge, GraphNode
from .projects import UvAdapter, select_adapter
from .resolution import ResolutionDecision, resolve_module

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "env", "build", "dist"}


def _is_test_file(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    return path.name.startswith("test_") or "tests" in parts or "test" in parts


def _scan_imports(root: Path) -> dict[str, list[str]]:
    imports: dict[str, list[str]] = {}
    for file_path in root.rglob("*.py"):
        if any(part in SKIP_DIRS or part.startswith("pytest-cache-files") for part in file_path.parts):
            continue
        try:
            source = file_path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.append(alias.name.split(".", 1)[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module.split(".", 1)[0])

        imports[str(file_path.relative_to(root)).replace("\\", "/")] = names
    return imports


def _declared_distributions(requirements_path: Path) -> set[str]:
    if not requirements_path.exists():
        return set()
    requirements = load_requirements(requirements_path.read_text(encoding="utf-8"))
    names = set()
    for line in requirements.text.splitlines():
        dependency = requirement_name_from_line(line)
        if dependency:
            names.add(dependency)
    return names


def build_dependency_graph(root: Path, failure: FailureObservation) -> tuple[DependencyGraphReport, ResolutionDecision]:
    adapter = select_adapter(root)
    declared = adapter.declared_dependencies(root)
    decision = resolve_module(failure.module or "", declared)

    import_map = _scan_imports(root)
    module_importers = [path for path, names in import_map.items() if (failure.module or "").split(".", 1)[0] in names]
    source_files = [path for path in module_importers if not _is_test_file(root / path)]
    test_files = [path for path in module_importers if _is_test_file(root / path)]

    blast_radius = BlastRadius(source_files=sorted(source_files), test_files=sorted(test_files))

    graph_nodes = [
        GraphNode(id="failure", kind="failure", label=failure.kind, metadata={"module": failure.module or ""}),
        GraphNode(id="module", kind="module", label=failure.module or "", metadata={}),
        GraphNode(id="manifest", kind="file", label=adapter.metadata(root).manifest_path, metadata={}),
    ]
    graph_edges = [
        GraphEdge(source="failure", target="module", label="observed"),
        GraphEdge(source="module", target="manifest", label="candidate dependency"),
    ]

    for distribution in sorted(declared):
        node_id = f"distribution-{distribution.lower()}"
        graph_nodes.append(GraphNode(id=node_id, kind="direct-distribution", label=distribution, metadata={}))
        graph_edges.append(GraphEdge(source=node_id, target="manifest", label="declared in"))

    for index, path in enumerate(blast_radius.source_files):
        node_id = f"source-{index}"
        graph_nodes.append(GraphNode(id=node_id, kind="source-file", label=path, metadata={"path": path}))
        graph_edges.append(GraphEdge(source=node_id, target="module", label="imports"))

    for index, path in enumerate(blast_radius.test_files):
        node_id = f"test-{index}"
        graph_nodes.append(GraphNode(id=node_id, kind="test-file", label=path, metadata={"path": path}))
        graph_edges.append(GraphEdge(source=node_id, target="module", label="imports"))

    report = DependencyGraphReport(
        repo_root=str(root),
        failure=failure,
        module=failure.module or "",
        declared_distribution=decision.proposed_distribution if decision.already_declared else None,
        proposed_distribution=decision.proposed_distribution,
        confidence=decision.confidence,
        rationale=decision.rationale,
        is_stdlib=decision.is_stdlib,
        is_ambiguous=decision.is_ambiguous,
        already_declared=decision.already_declared,
        blast_radius=blast_radius,
        graph_nodes=graph_nodes,
        graph_edges=graph_edges,
    )
    if isinstance(adapter, UvAdapter):
        packages = adapter.locked_packages(root)
        for package in packages:
            package_id = f"locked-{package.name.lower()}"
            graph_nodes.append(
                GraphNode(
                    id=package_id,
                    kind="resolved-distribution",
                    label=f"{package.name}=={package.version}",
                    metadata={"source": package.source or ""},
                )
            )
            for child in package.dependencies:
                graph_edges.append(GraphEdge(source=package_id, target=f"locked-{child.lower()}", label="depends on"))
        if decision.proposed_distribution:
            report.graph_edges.extend([])
    return report, decision


def graph_to_dict(report: DependencyGraphReport) -> dict[str, object]:
    return {
        "repo_root": report.repo_root,
        "failure": asdict(report.failure),
        "module": report.module,
        "declared_distribution": report.declared_distribution,
        "proposed_distribution": report.proposed_distribution,
        "confidence": report.confidence,
        "rationale": report.rationale,
        "is_stdlib": report.is_stdlib,
        "is_ambiguous": report.is_ambiguous,
        "already_declared": report.already_declared,
        "blast_radius": {
            "source_files": report.blast_radius.source_files,
            "test_files": report.blast_radius.test_files,
            "source_count": report.blast_radius.source_count,
            "test_count": report.blast_radius.test_count,
        },
        "graph_nodes": [asdict(node) for node in report.graph_nodes],
        "graph_edges": [asdict(edge) for edge in report.graph_edges],
    }


def graph_to_json(report: DependencyGraphReport) -> str:
    return json.dumps(graph_to_dict(report), indent=2, sort_keys=True)


def graph_to_text(report: DependencyGraphReport) -> str:
    lines = [
        f"Failure: {report.failure.kind} for {report.module}",
        f"Proposed distribution: {report.proposed_distribution or 'none'}",
        f"Confidence: {report.confidence:.2f}",
        f"Reason: {report.rationale}",
        f"Already declared: {'yes' if report.already_declared else 'no'}",
        f"Stdlib: {'yes' if report.is_stdlib else 'no'}",
        f"Source files: {len(report.blast_radius.source_files)}",
        f"Test files: {len(report.blast_radius.test_files)}",
    ]
    if report.blast_radius.source_files:
        lines.append("Source imports:")
        lines.extend(f"  - {path}" for path in report.blast_radius.source_files)
    if report.blast_radius.test_files:
        lines.append("Test imports:")
        lines.extend(f"  - {path}" for path in report.blast_radius.test_files)
    return "\n".join(lines)


def graph_to_dot(report: DependencyGraphReport) -> str:
    lines = ["digraph ci_janitor {", "  rankdir=LR;"]
    for node in report.graph_nodes:
        label = node.label.replace('"', '\\"')
        lines.append(f'  "{node.id}" [label="{label}", shape="box"];')
    for edge in report.graph_edges:
        label = edge.label.replace('"', '\\"')
        lines.append(f'  "{edge.source}" -> "{edge.target}" [label="{label}"];')
    lines.append("}")
    return "\n".join(lines)
