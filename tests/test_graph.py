from pathlib import Path

from cifixagent.graph import build_dependency_graph, graph_to_dot, graph_to_json, graph_to_text
from cifixagent.parsing import parse_failure


def make_repo(root: Path) -> None:
    (root / "requirements.txt").write_text("# fixtures only\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src" / "app.py").write_text("import yaml\n", encoding="utf-8")
    (root / "tests" / "test_app.py").write_text("import yaml\n", encoding="utf-8")


def test_graph_reports_source_and_test_blast_radius(tmp_path):
    make_repo(tmp_path)
    failure = parse_failure("ModuleNotFoundError: No module named 'yaml'")
    assert failure is not None

    report, decision = build_dependency_graph(tmp_path, failure)

    assert decision.proposed_distribution == "PyYAML"
    assert report.blast_radius.source_files == ["src/app.py"]
    assert report.blast_radius.test_files == ["tests/test_app.py"]
    assert report.already_declared is False
    assert report.proposed_distribution == "PyYAML"

    text = graph_to_text(report)
    assert "Source files: 1" in text
    assert "Test files: 1" in text

    data = graph_to_json(report)
    assert '"proposed_distribution": "PyYAML"' in data

    dot = graph_to_dot(report)
    assert "digraph ci_janitor" in dot
    assert "failure" in dot


def test_graph_does_not_claim_transitive_dependencies(tmp_path):
    make_repo(tmp_path)
    failure = parse_failure("ModuleNotFoundError: No module named 'yaml'")
    assert failure is not None
    report, _ = build_dependency_graph(tmp_path, failure)
    text = graph_to_text(report)
    assert "transitive" not in text.lower()
