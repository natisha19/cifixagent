from cifixagent.resolution import EXPLICIT_MAPPINGS, resolve_module


def test_all_explicit_mappings_are_high_confidence():
    for module, distribution in EXPLICIT_MAPPINGS.items():
        result = resolve_module(module, set())
        assert result.proposed_distribution == distribution
        assert result.confidence >= 0.9
        assert not result.is_stdlib
        assert not result.is_ambiguous


def test_standard_library_needs_no_proposal():
    result = resolve_module("json", set())

    assert result.is_stdlib
    assert result.proposed_distribution is None
    assert result.confidence == 1.0


def test_unknown_module_is_low_confidence_and_not_auto_applied():
    result = resolve_module("mystery_module", set())

    assert result.proposed_distribution is None
    assert result.is_ambiguous
    assert result.confidence < 0.5


def test_case_insensitive_already_declared_detection():
    result = resolve_module("yaml", {"pyyaml"})

    assert result.already_declared
    assert result.proposed_distribution == EXPLICIT_MAPPINGS["yaml"]


def test_nested_module_uses_top_level():
    result = resolve_module("PIL.Image", set())
    assert result.proposed_distribution == "Pillow"
