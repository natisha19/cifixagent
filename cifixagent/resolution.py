from __future__ import annotations

import builtins
import re
import sys
from dataclasses import dataclass

EXPLICIT_MAPPINGS: dict[str, str] = {
    "yaml": "PyYAML",
    "PIL": "Pillow",
    "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn",
    "dotenv": "python-dotenv",
    "dateutil": "python-dateutil",
    "cv2": "opencv-python",
    "Crypto": "pycryptodome",
}

stdlib_names = set(sys.stdlib_module_names)
stdlib_names.update(name for name in dir(builtins) if not name.startswith("__"))


def canonical_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).casefold()


def top_level_module(name: str) -> str:
    return name.split(".", 1)[0].strip()


@dataclass(slots=True)
class ResolutionDecision:
    module: str
    proposed_distribution: str | None
    confidence: float
    rationale: str
    is_stdlib: bool = False
    is_ambiguous: bool = False
    already_declared: bool = False


def resolve_module(module_name: str, declared_distributions: set[str]) -> ResolutionDecision:
    module = top_level_module(module_name)
    normalized_declared = {canonical_distribution_name(name) for name in declared_distributions}

    if module in stdlib_names:
        return ResolutionDecision(
            module=module,
            proposed_distribution=None,
            confidence=1.0,
            rationale="The missing module is part of the Python standard library.",
            is_stdlib=True,
            already_declared=False,
        )

    if canonical_distribution_name(module) in normalized_declared:
        return ResolutionDecision(
            module=module,
            proposed_distribution=module,
            confidence=1.0,
            rationale="The module name already appears in requirements.txt.",
            already_declared=True,
        )

    if module in EXPLICIT_MAPPINGS:
        distribution = EXPLICIT_MAPPINGS[module]
        if canonical_distribution_name(distribution) in normalized_declared:
            return ResolutionDecision(
                module=module,
                proposed_distribution=distribution,
                confidence=0.97,
                rationale=f"Explicit mapping {module} -> {distribution}; the distribution is already declared.",
                already_declared=True,
            )
        return ResolutionDecision(
            module=module,
            proposed_distribution=distribution,
            confidence=0.97,
            rationale=f"Explicit high-confidence mapping {module} -> {distribution}.",
        )

    return ResolutionDecision(
        module=module,
        proposed_distribution=None,
        confidence=0.25,
        rationale="No explicit mapping exists; the module is not proposed automatically.",
        is_ambiguous=True,
    )
