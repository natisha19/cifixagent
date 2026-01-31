import os
import requests
import zipfile
import io
from pathlib import Path

# =========================
# GitHub MCP Tool
# =========================
class GitHubTool:
    def __init__(self):
        self.token = os.environ["GITHUB_TOKEN"]
        self.repo = os.environ["REPO"]
        self.run_id = os.environ["RUN_ID"]
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json"
        }

    def get_ci_logs(self) -> str:
        """
        Fetch logs for the failed CI workflow run.
        (Maps to GitHub MCP Server: read CI logs)
        """
        url = f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}/logs"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()

        zip_file = zipfile.ZipFile(io.BytesIO(response.content))
        logs = ""
        for name in zip_file.namelist():
            logs += zip_file.read(name).decode("utf-8", errors="ignore")

        return logs

    def post_pr_comment(self, body: str):
        """
        Post a comment on the associated PR.
        (Maps to GitHub MCP Server: PR comments)
        """
        run_url = f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}"
        run = requests.get(run_url, headers=self.headers).json()

        if not run.get("pull_requests"):
            print("No PR associated with this workflow run.")
            return

        pr_number = run["pull_requests"][0]["number"]
        comment_url = f"https://api.github.com/repos/{self.repo}/issues/{pr_number}/comments"

        requests.post(
            comment_url,
            headers=self.headers,
            json={"body": body}
        )


# =========================
# Filesystem MCP Tool
# =========================
class FilesystemTool:
    def add_dependency(self, dependency: str):
        """
        Apply a minimal patch to requirements.txt
        (Maps to Filesystem MCP Server: apply patch)
        """
        req = Path("requirements.txt")
        content = req.read_text()

        if dependency not in content:
            req.write_text(content + f"\n{dependency}\n")


# =========================
# Agent Reasoning Core
# =========================
class CIFixAgent:
    def __init__(self):
        self.github = GitHubTool()
        self.fs = FilesystemTool()

    def diagnose(self, logs: str):
        """
        Decide WHAT is wrong.
        (Pure reasoning, no side effects)
        """
        if "ModuleNotFoundError" in logs:
            missing = logs.split("No module named")[-1].strip().strip("'\"")
            return {
                "type": "missing_dependency",
                "dependency": missing
            }

        return {"type": "unknown"}

    def act(self, diagnosis):
        """
        Decide HOW to fix it.
        (Calls MCP tools)
        """
        if diagnosis["type"] == "missing_dependency":
            dep = diagnosis["dependency"]
            self.fs.add_dependency(dep)

            comment = f"""
🤖 **CI Janitor Report**

**Error Detected**
- Missing Python dependency: `{dep}`

**Root Cause**
- Dependency not listed in `requirements.txt`

**Proposed Fix**
- Added `{dep}` to dependencies

**Status**
- Awaiting human approval before merge
"""
            self.github.post_pr_comment(comment)

            print(f"✔ Proposed fix for missing dependency: {dep}")
        else:
            print("No fixable CI hygiene issue detected.")

    def run(self):
        logs = self.github.get_ci_logs()
        diagnosis = self.diagnose(logs)
        self.act(diagnosis)


# =========================
# Entry Point
# =========================
if __name__ == "__main__":
    agent = CIFixAgent()
    agent.run()
