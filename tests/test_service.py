import io
import zipfile
from pathlib import Path

from cifixagent.github import GitHubClient, HTTPResponse, _decode_logs_payload
from cifixagent.service import CIJanitorService
from cifixagent.validation import ValidationResult, ValidationSettings, Validator


class FakeValidator(Validator):
    def __init__(self, result: ValidationResult):
        self.result = result
        self.calls: list[tuple[Path, object, ValidationSettings]] = []

    def validate(self, repo_root: Path, proposal, settings: ValidationSettings) -> ValidationResult:
        self.calls.append((repo_root, proposal, settings))
        return self.result


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        return self.responses.pop(0)


def make_repo(root: Path) -> None:
    (root / "requirements.txt").write_text("# fixture\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("import yaml\n", encoding="utf-8")


def test_unknown_dependency_refusal(tmp_path):
    make_repo(tmp_path)
    service = CIJanitorService(
        tmp_path, FakeValidator(ValidationResult(True, ["pytest"], "tmp", 0, "", ""))
    )

    proposal = service.propose("ModuleNotFoundError: No module named 'mystery_module'")

    assert proposal.proposed_distribution is None
    assert proposal.safe_to_apply is False
    assert proposal.refusal_reason is not None


def test_stdlib_refusal(tmp_path):
    make_repo(tmp_path)
    service = CIJanitorService(
        tmp_path, FakeValidator(ValidationResult(True, ["pytest"], "tmp", 0, "", ""))
    )
    proposal = service.propose("ModuleNotFoundError: No module named 'json'")
    assert proposal.safe_to_apply is False
    assert "standard library" in (proposal.refusal_reason or "").lower()


def test_already_declared_refusal(tmp_path):
    (tmp_path / "requirements.txt").write_text("PyYAML==6.0.2\n", encoding="utf-8")
    service = CIJanitorService(
        tmp_path, FakeValidator(ValidationResult(True, ["pytest"], "tmp", 0, "", ""))
    )
    proposal = service.propose("ModuleNotFoundError: No module named 'yaml'")
    assert proposal.safe_to_apply is False
    assert "already declared" in (proposal.refusal_reason or "").lower()


def test_validation_success_marks_proposal_safe(tmp_path):
    make_repo(tmp_path)
    service = CIJanitorService(
        tmp_path,
        FakeValidator(ValidationResult(True, ["python", "-m", "pytest"], "tmp", 0, "ok", "")),
    )

    proposal = service.propose("ModuleNotFoundError: No module named 'yaml'")
    result = service.validate(proposal, ["python", "-m", "pytest"])

    assert result.success is True
    assert proposal.safe_to_apply is True
    assert proposal.validation_result is result


def test_validation_failure_refuses_apply(tmp_path):
    make_repo(tmp_path)
    service = CIJanitorService(
        tmp_path,
        FakeValidator(ValidationResult(False, ["python", "-m", "pytest"], "tmp", 1, "", "boom")),
    )

    proposal = service.propose("ModuleNotFoundError: No module named 'yaml'")
    result = service.validate(proposal, ["python", "-m", "pytest"])

    assert result.success is False
    assert proposal.safe_to_apply is False


def test_exact_approval_comment_and_permission_gate(tmp_path):
    service = CIJanitorService(
        tmp_path, FakeValidator(ValidationResult(True, ["pytest"], "tmp", 0, "", ""))
    )
    pull_request = {
        "head": {"repo": {"full_name": "owner/repo"}},
        "base": {"repo": {"full_name": "owner/repo"}},
    }

    approved = service.authorize_comment(
        body="/ci-janitor approve",
        commenter_login="alice",
        pull_request=pull_request,
        permission_lookup=lambda _: "write",
    )

    rejected = service.authorize_comment(
        body="please apply",
        commenter_login="alice",
        pull_request=pull_request,
        permission_lookup=lambda _: "write",
    )

    read_only = service.authorize_comment(
        body="/ci-janitor approve",
        commenter_login="bob",
        pull_request=pull_request,
        permission_lookup=lambda _: "read",
    )

    assert approved.approved is True
    assert rejected.approved is False
    assert "exactly" in rejected.reason
    assert read_only.approved is False
    assert "write" in read_only.reason.lower()


def test_fork_refusal_blocks_apply(tmp_path):
    service = CIJanitorService(
        tmp_path, FakeValidator(ValidationResult(True, ["pytest"], "tmp", 0, "", ""))
    )
    pull_request = {
        "head": {"repo": {"full_name": "fork/repo"}},
        "base": {"repo": {"full_name": "owner/repo"}},
    }

    decision = service.authorize_comment(
        body="/ci-janitor approve",
        commenter_login="alice",
        pull_request=pull_request,
        permission_lookup=lambda _: "admin",
    )

    assert decision.approved is False
    assert "Fork pull requests" in decision.reason


def test_github_client_uses_mocked_http_and_prevents_duplicate_comments():
    responses = [
        HTTPResponse(200, b'[{"id": 7, "body": "<!-- ci-janitor:proposal --> old"}]', {}),
        HTTPResponse(200, b'{"id": 7, "body": "<!-- ci-janitor:proposal --> new"}', {}),
    ]
    transport = FakeTransport(responses)
    client = GitHubClient(token="token", repo="owner/repo", transport=transport)

    body = client.ensure_comment(
        12, "<!-- ci-janitor:proposal --> new", "<!-- ci-janitor:proposal -->"
    )

    assert body["id"] == 7
    assert len(transport.calls) == 2
    assert transport.calls[0][0] == "GET"
    assert transport.calls[1][0] == "PATCH"


def test_decode_zip_logs_without_network():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("job/1_Test.txt", "ModuleNotFoundError: No module named 'yaml'\n")
    text = _decode_logs_payload(buffer.getvalue())
    assert "yaml" in text


def test_apply_locally_only_when_safe(tmp_path):
    make_repo(tmp_path)
    service = CIJanitorService(
        tmp_path,
        FakeValidator(ValidationResult(True, ["python", "-m", "pytest"], "tmp", 0, "ok", "")),
    )
    proposal = service.propose("ModuleNotFoundError: No module named 'yaml'")
    service.validate(proposal, ["python", "-m", "pytest"])
    service.apply_locally(proposal)
    content = (tmp_path / "requirements.txt").read_text(encoding="utf-8")
    assert "PyYAML" in content


def test_proposal_comment_includes_approval_command(tmp_path):
    make_repo(tmp_path)
    service = CIJanitorService(
        tmp_path, FakeValidator(ValidationResult(True, ["pytest"], "tmp", 0, "", ""))
    )
    proposal = service.propose("ModuleNotFoundError: No module named 'yaml'")
    comment = proposal.render_comment("https://example.com/run/1")
    assert "<!-- ci-janitor:proposal -->" in comment
    assert "/ci-janitor approve" in comment
    assert "PyYAML" in comment
