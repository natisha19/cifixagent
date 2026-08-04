from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request

LOGGER = logging.getLogger("cifixagent.github")


class GitHubHTTPError(RuntimeError):
    pass


@dataclass(slots=True)
class HTTPResponse:
    status: int
    body: bytes
    headers: dict[str, str]


class Transport(Protocol):
    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> HTTPResponse: ...


def default_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> HTTPResponse:
    req = request.Request(url=url, method=method, headers=headers, data=body)
    try:
        with request.urlopen(req, timeout=30) as response:
            payload = response.read()
            response_headers = {key: value for key, value in response.headers.items()}
            return HTTPResponse(status=response.status, body=payload, headers=response_headers)
    except error.HTTPError as exc:
        payload = exc.read() if hasattr(exc, "read") else b""
        raise GitHubHTTPError(
            f"GitHub API request failed with status {exc.code}: {payload[:200]!r}"
        ) from exc


@dataclass(slots=True)
class GitHubClient:
    token: str
    repo: str
    transport: Transport = default_transport
    api_base: str = "https://api.github.com"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ci-janitor",
        }

    def _request(self, method: str, path: str, body: dict[str, object] | None = None) -> HTTPResponse:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        return self.transport(method, f"{self.api_base}{path}", self._headers(), payload)

    def _request_json(self, method: str, path: str, body: dict[str, object] | None = None) -> Any:
        response = self._request(method, path, body)
        if response.status >= 400:
            raise GitHubHTTPError(f"GitHub API request failed with status {response.status}")
        if not response.body:
            return {}
        return json.loads(response.body.decode("utf-8"))

    def get_pull_request(self, number: int) -> dict[str, Any]:
        return self._request_json("GET", f"/repos/{self.repo}/pulls/{number}")

    def list_workflow_runs(self) -> dict[str, Any]:
        return self._request_json("GET", f"/repos/{self.repo}/actions/runs?per_page=50")

    def get_workflow_run(self, run_id: int | str) -> dict[str, Any]:
        return self._request_json("GET", f"/repos/{self.repo}/actions/runs/{run_id}")

    def get_workflow_logs(self, run_id: int | str) -> str:
        response = self.transport(
            "GET",
            f"{self.api_base}/repos/{self.repo}/actions/runs/{run_id}/logs",
            self._headers(),
            None,
        )
        if response.status >= 400:
            raise GitHubHTTPError(f"GitHub logs request failed with status {response.status}")
        return _decode_logs_payload(response.body)

    def post_comment(self, issue_number: int, body: str) -> dict[str, Any]:
        return self._request_json(
            "POST", f"/repos/{self.repo}/issues/{issue_number}/comments", {"body": body}
        )

    def create_pull_request(self, *, head: str, base: str, title: str, body: str) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/repos/{self.repo}/pulls",
            {"head": head, "base": base, "title": title, "body": body},
        )

    def update_comment(self, comment_id: int, body: str) -> dict[str, Any]:
        return self._request_json(
            "PATCH", f"/repos/{self.repo}/issues/comments/{comment_id}", {"body": body}
        )

    def list_comments(self, issue_number: int) -> list[dict[str, Any]]:
        response = self._request_json(
            "GET", f"/repos/{self.repo}/issues/{issue_number}/comments?per_page=100"
        )
        if isinstance(response, list):
            return response
        return []

    def get_collaborator_permission(self, username: str) -> str:
        response = self._request_json(
            "GET", f"/repos/{self.repo}/collaborators/{username}/permission"
        )
        return str(response.get("permission", "none"))

    def ensure_comment(self, issue_number: int, body: str, marker: str) -> dict[str, Any]:
        for comment in self.list_comments(issue_number):
            if marker in str(comment.get("body", "")):
                comment_id = int(comment["id"])
                LOGGER.info("Updating existing marker comment %s", comment_id)
                return self.update_comment(comment_id, body)
        LOGGER.info("Creating new marker comment on issue %s", issue_number)
        return self.post_comment(issue_number, body)

    def base_and_head_same_repo(self, pull_request: dict[str, Any]) -> bool:
        head = pull_request.get("head", {})
        head_repo = head.get("repo") or {}
        return str(head_repo.get("full_name", "")) == self.repo


def _decode_logs_payload(payload: bytes) -> str:
    if payload[:2] == b"PK":
        logs: list[str] = []
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for name in archive.namelist():
                logs.append(archive.read(name).decode("utf-8", errors="replace"))
        return "\n".join(logs)
    return payload.decode("utf-8", errors="replace")
