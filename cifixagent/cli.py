from __future__ import annotations

import argparse
import json
import logging
import shlex
import sys
from pathlib import Path

from .graph import build_dependency_graph, graph_to_dot, graph_to_json, graph_to_text
from .parsing import parse_failure
from .service import CIJanitorService
from .validation import CopyWorkspaceValidator

LOGGER = logging.getLogger("cifixagent")


def _read_logs(args: argparse.Namespace) -> str:
    if getattr(args, "logs_file", None):
        return Path(args.logs_file).read_text(encoding="utf-8")
    return args.logs or ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cifixagent",
        description="CI Janitor: diagnose and propose safe Python dependency CI fixes.",
    )
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Parse logs and explain the failure")
    analyze.add_argument("--logs", default="")
    analyze.add_argument("--logs-file")
    analyze.add_argument("--repo", required=True)
    analyze.add_argument("--format", choices=("text", "json"), default="text")
    analyze.set_defaults(func=_cmd_analyze)

    graph = subparsers.add_parser("graph", help="Build the dependency graph")
    graph.add_argument("--repo", required=True)
    graph.add_argument("--logs", default="")
    graph.add_argument("--logs-file")
    graph.add_argument("--format", choices=("text", "json"), default="text")
    graph.add_argument("--dot", help="Optional path to write Graphviz DOT output")
    graph.set_defaults(func=_cmd_graph)

    propose = subparsers.add_parser("propose", help="Create a fix proposal")
    propose.add_argument("--logs", default="")
    propose.add_argument("--logs-file")
    propose.add_argument("--repo", required=True)
    propose.add_argument("--format", choices=("text", "json"), default="text")
    propose.set_defaults(func=_cmd_propose)

    validate = subparsers.add_parser(
        "validate", help="Validate a proposal in a temporary workspace"
    )
    validate.add_argument("--logs", default="")
    validate.add_argument("--logs-file")
    validate.add_argument("--repo", required=True)
    validate.add_argument(
        "--apply",
        action="store_true",
        help="Apply the validated proposal to --repo (requires --yes)",
    )
    validate.add_argument(
        "--yes",
        action="store_true",
        help="Confirm mutation when used with --apply",
    )
    validate.add_argument(
        "--allow-network",
        action="store_true",
        help="Opt in to network-capable validation commands (off by default)",
    )
    validate.add_argument(
        "--command",
        default="python -m pytest",
        help='Validation command string (default: "python -m pytest")',
    )
    validate.set_defaults(func=_cmd_validate)

    return parser


def _service(repo: str) -> CIJanitorService:
    return CIJanitorService(Path(repo), CopyWorkspaceValidator())


def _cmd_analyze(args: argparse.Namespace) -> int:
    logs = _read_logs(args)
    failure = parse_failure(logs)
    if failure is None:
        if args.format == "json":
            print(json.dumps({"failure": None}, indent=2))
        else:
            print("No supported dependency failure detected.")
        return 1

    report, _ = build_dependency_graph(Path(args.repo), failure)
    if args.format == "json":
        print(graph_to_json(report))
    else:
        print(graph_to_text(report))
    return 0


def _cmd_graph(args: argparse.Namespace) -> int:
    logs = _read_logs(args)
    failure = parse_failure(logs)
    if failure is None:
        print("No supported dependency failure detected.")
        return 1

    report, _ = build_dependency_graph(Path(args.repo), failure)
    if args.dot:
        Path(args.dot).write_text(graph_to_dot(report), encoding="utf-8")
    if args.format == "json":
        print(graph_to_json(report))
    else:
        print(graph_to_text(report))
    return 0


def _cmd_propose(args: argparse.Namespace) -> int:
    service = _service(args.repo)
    proposal = service.propose(_read_logs(args))
    if args.format == "json":
        print(json.dumps(proposal.to_dict(), indent=2, sort_keys=True))
    else:
        print(proposal.to_text())
    return 0 if proposal.proposed_distribution and proposal.requirement_change else 1


def _cmd_validate(args: argparse.Namespace) -> int:
    service = _service(args.repo)
    proposal = service.propose(_read_logs(args))

    if proposal.proposed_distribution is None or proposal.requirement_change is None:
        print(proposal.refusal_reason or "No supported dependency failure detected.")
        return 1

    command = shlex.split(args.command)

    result = service.validate(
        proposal,
        command,
        allow_network=bool(args.allow_network),
    )
    print(proposal.to_text())
    if not result.success:
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)

    if args.apply:
        if not args.yes:
            print("Refusing to apply without --yes.")
            return 3
        if not proposal.safe_to_apply or not result.success:
            print("Validation failed or proposal unsafe; refusing to apply.")
            return 2
        service.apply_locally(proposal)
        print(f"Applied {proposal.proposed_distribution} to requirements.txt")
        return 0

    return 0 if result.success else 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
