# CI Remediation Agent

An autonomous **CI failure diagnosis and repair agent** that monitors failing GitHub Actions workflows, analyzes logs to identify root causes (e.g., missing dependencies), and proposes safe fixes directly on pull requests — with a human-in-the-loop approval workflow.

## Overview

Modern CI pipelines often fail due to simple issues like missing dependencies, but developers still need to manually debug logs, identify the problem, and push fixes.

This project automates that loop:

1. Detect CI failure
2. Analyze logs
3. Propose fix on PR
4. Wait for approval
5. Apply fix automatically
6. Re-run CI

✅ Reduces debugging time
✅ Keeps humans in control
✅ Ensures safe, auditable fixes

---

## How It Works

### 🔹 Phase 1 — Diagnose (on CI failure)

* Triggered when the **CI workflow fails**
* Fetches logs from GitHub Actions
* Extracts errors like:

  ```
  ModuleNotFoundError: No module named 'requests'
  ```
* Posts a PR comment:

  > Found missing dependency `requests`
  > Reply `/ci-janitor approve` to apply fix

---

### 🔹 Phase 2 — Apply Fix (on approval)

* Triggered when a PR comment contains:

  ```
  /ci-janitor approve
  ```
* Agent:

  * Updates `requirements.txt`
  * Commits fix to PR branch
  * Pushes changes
  * CI automatically re-runs

---

## Architecture

The system follows a **modular agent design (MCP-style)**:

### Components

* **GitHub Tool**

  * Fetch CI logs
  * Read PR metadata
  * Post comments

* **Filesystem Tool**

  * Modify `requirements.txt`

* **Agent Core**

  * Diagnose failures from logs
  * Decide actions
  * Execute fixes after approval

---

## Workflow

```
CI fails
   ↓
Agent analyzes logs
   ↓
Agent comments on PR
   ↓
User approves (/ci-janitor approve)
   ↓
Agent applies fix
   ↓
Commit + Push
   ↓
CI re-runs ✅
```

---

## Project Structure

```
cifixagent/
├── app/
│   └── __init__.py
├── agent.py
├── requirements.txt
├── tests/
│   └── test_app.py
└── .github/workflows/
    ├── ci.yml
    └── auto_fix.yml
```

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/your-username/cifixagent.git
cd cifixagent
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run tests

```bash
pytest
```

---

## GitHub Actions Setup

The agent requires:

### Permissions

```yaml
permissions:
  contents: write
  pull-requests: write
  actions: read
```

### Workflows

* `ci.yml` → runs tests
* `auto_fix.yml` → runs agent

---

## Example

### CI Failure

```
ModuleNotFoundError: No module named 'requests'
```

### Agent Comment

```
CI Janitor

Found missing dependency `requests`.

Reply with /ci-janitor approve to apply the fix.
```

### After Approval

* `requests` added to `requirements.txt`
* Commit pushed
* CI passes ✅

---

## Key Features

*  **Log-driven diagnosis**
*  **Deterministic reasoning (no hardcoding)**
*  **Human-in-the-loop approval**
*  **Automatic CI recovery**
*  **Modular MCP-style architecture**
*  **Safe, minimal patches only**

---

## Planned Future Improvements

* Iterative multi-error fixing
* Version inference for dependencies
* LLM-assisted root cause analysis
* Support for multiple languages
* CI failure analytics dashboard

---

## Why This Matters

CI failures slow down development workflows.
This agent reduces friction by **closing the loop between failure and fix** — safely and automatically.

## Authored by Team DietCode

