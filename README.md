# CI Janitor

Security-conscious Python CI remediation assistant for **common dependency/environment failures**.

CI Janitor diagnoses resolver and import failures for `requirements.txt` and `pyproject.toml` + `uv.lock` projects, explains direct and resolved dependency impact, proposes the **smallest safe patch**, validates it in an isolated workspace, and **requires authorized human approval** before opening a remediation PR.

---

## Problem

Python CI often fails because an import is missing from `requirements.txt`. Bots that auto-commit fixes are risky: they may execute untrusted PR code with write credentials, guess wrong packages, or broaden blast radius without review.

---

## Architecture

```text
CI logs ──► parsing.py ──► FailureObservation
                │
                ▼
         resolution.py ──► import → distribution (explicit map / stdlib / unknown)
                │
                ▼
           graph.py ──► declared deps + importers + blast radius
                │
                ▼
          service.py ──► FixProposal (safe_to_apply gate)
                │
        ┌───────┴────────┐
        ▼                ▼
  validation.py     github.py / gitops.py
  (temp copy)       (comment / approve / push)
        │
        ▼
      cli.py / workflow.py
```

| Module | Responsibility |
|--------|----------------|
| `parsing.py` | Extract failures; sanitize/bound log excerpts |
| `resolution.py` | Stdlib / explicit mappings / low-confidence unknowns |
| `dependencies.py` | Parse/edit `requirements.txt` preserving comments/pins |
| `projects.py` | Detect pip/uv projects; parse PEP 621 declarations and `uv.lock` |
| `graph.py` | Nodes/edges, blast radius, text/JSON/DOT |
| `validation.py` | Isolated copy + timed command (network opt-in) |
| `github.py` | Injected HTTP transport; comments; permissions |
| `gitops.py` | Stage only expected file; confirm diff; push |
| `service.py` | Orchestration + approval authorization |
| `cli.py` | Local CLI |
| `workflow.py` | GitHub Actions propose/apply entrypoints |

Interfaces keep subprocess and GitHub I/O behind injectable seams so unit tests never hit the network.

---

## Safety / threat model

**Assets:** repository contents, GitHub write tokens, PR branches.

**Threats:** untrusted PR code execution, package confusion, fork PR token abuse, comment spoofing, log exfiltration, duplicate noisy bots.

**Controls (MVP):**

1. Never execute PR-head automation code with write credentials.
2. Trusted agent code is installed from the **default branch** (or a pinned trusted commit).
3. Separate **propose** and **apply** workflows.
4. PR checkout is analyzed/edited as **data**; validation strips GitHub credentials before project code executes.
5. Approval must match exactly: `/ci-janitor approve`.
6. Commenter must have GitHub `write` / `maintain` / `admin` permission.
7. Fork PRs are refused for apply in MVP.
8. Workflows use explicit least-privilege `permissions` and concurrency groups.
9. Stable marker `<!-- ci-janitor:proposal -->` prevents duplicate proposal comments.
10. Git apply stages only approved dependency manifests/lockfiles and confirms content equals the proposal.
11. A validated same-repo change is pushed to a dedicated bot branch and opened as a remediation PR.
12. Log excerpts are sanitized/bounded; comments link the workflow run instead of dumping full logs.

Unknown modules are reported at low confidence and **never** auto-applied. CI Janitor does not query PyPI to guess mappings.

---

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Unix:    source .venv/bin/activate
pip install -e ".[dev]"
pip install -r requirements.txt
pytest
```

Optional: `ruff check cifixagent tests`, `mypy cifixagent`, `coverage run -m pytest`.

---

## CLI

```bash
python -m cifixagent analyze --logs "ModuleNotFoundError: No module named 'yaml'" --repo .
python -m cifixagent graph --repo . --logs-file ci.log --format json
python -m cifixagent graph --repo . --logs-file ci.log --dot graph.dot
python -m cifixagent propose --logs-file ci.log --repo . --format text
python -m cifixagent validate --logs-file ci.log --repo . --command -- python -m pytest
python -m cifixagent validate --logs-file ci.log --repo . --allow-network --apply --yes
```

Exit codes:

| Code | Meaning |
|------|---------|
| `0` | Success / safe proposal validated |
| `1` | No supported failure / no high-confidence proposal |
| `2` | Validation failed or unsafe apply refused |
| `3` | `--apply` without `--yes` |

No mutation without `--apply`. Apply additionally requires `--yes`.

---

## Example output

```text
Failure: ModuleNotFoundError for yaml
Proposed distribution: PyYAML
Confidence: 0.97
Why: Explicit high-confidence mapping yaml -> PyYAML.
Already declared: no
Stdlib: no
Blast radius: 1 source files, 1 test files
Safe to apply: yes
Validation: passed (python -m pytest)
```

PR comment (abbreviated):

```markdown
<!-- ci-janitor:proposal -->
## CI Janitor analysis

**Failure:** `ModuleNotFoundError` for `yaml`
**Mapping:** `yaml` -> `PyYAML` (confidence 0.97)
**Why:** Explicit high-confidence mapping yaml -> PyYAML.
**Minimal change:** requirements.txt
**Graph impact / blast radius:** 1 source files, 1 test files ...
**Validation:** passed using python -m pytest
**Approval:** reply with exactly `/ci-janitor approve`
**Workflow run:** https://github.com/org/repo/actions/runs/123
```

---

## Dependency intelligence

The graph represents:

- declared distributions from `requirements.txt`
- imports/modules discovered via AST
- import→distribution mapping decision
- observed failure node
- source/test files that import the failing module (simple blast radius)

It answers:

- Is the package already declared?
- Is this standard library?
- Is the mapping known or ambiguous?
- What direct dependency change is proposed?
- Which repo files/tests import it?
- What is the simple blast radius?

For uv projects, it reads `uv.lock` and shows resolved package nodes and dependency paths. Pip-only projects never claim transitive knowledge.

### Resolver mappings (high confidence)

| Import | Distribution |
|--------|--------------|
| `yaml` | `PyYAML` |
| `PIL` | `Pillow` |
| `bs4` | `beautifulsoup4` |
| `sklearn` | `scikit-learn` |
| `dotenv` | `python-dotenv` |
| `dateutil` | `python-dateutil` |
| `cv2` | `opencv-python` |
| `Crypto` | `pycryptodome` |

Rules: stdlib → no proposal; explicit map → high confidence; unknown → low confidence, report only.

---

## GitHub Actions

- `.github/workflows/ci.yml` — package tests
- `.github/workflows/ci-janitor-propose.yml` — on failed CI `workflow_run`, trusted agent proposes
- `.github/workflows/ci-janitor-apply.yml` — on exact `/ci-janitor approve`, re-validates and pushes

Demo fixture: `demo/missing_dep.py` (`import yaml`).

---

## Limitations

- Poetry and npm are unsupported; pip and `pyproject.toml` + `uv.lock` are supported
- `ModuleNotFoundError` / supported `ImportError` only
- No PyPI guessing; unknown imports are never auto-applied
- No fork-PR apply
- Pip-only projects have no transitive dependency analysis; uv analysis depends on the committed `uv.lock`
- Validation requires explicit `--allow-network` and a repository-configured index; it never falls back to public PyPI
- Does not fix arbitrary test/logic/CI YAML failures

---

## Roadmap

- Optional lockfile-aware transitive graphs
- Poetry / uv / pep621 adapters behind the same interfaces
- Richer ImportError classes (ABI / wrong version)
- Signed/pinned agent releases
- Policy packs per org (allowed distributions)
