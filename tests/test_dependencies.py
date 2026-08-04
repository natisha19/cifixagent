from cifixagent.dependencies import RequirementsFile, requirement_name_from_line


def test_requirements_add_preserves_comments_blank_lines_and_pins():
    text = """# core dependency
requests==2.32.3

# pinned sample
PyYAML==6.0.2
"""

    requirements = RequirementsFile(text)
    updated, line_number, changed = requirements.add("beautifulsoup4")

    assert changed is True
    assert line_number == 7
    assert "# core dependency" in updated
    assert "requests==2.32.3" in updated
    assert "\n\n# pinned sample\n" in updated
    assert updated.endswith("beautifulsoup4\n")


def test_requirements_do_not_duplicate_case_insensitive_names():
    requirements = RequirementsFile("Requests==2.32.3\n")

    updated, line_number, changed = requirements.add("requests")

    assert changed is False
    assert updated == "Requests==2.32.3\n"
    assert line_number == 1


def test_requirement_name_parsing_ignores_directives_and_comments():
    assert requirement_name_from_line("# comment") is None
    assert requirement_name_from_line("--index-url https://example.com") is None
    assert requirement_name_from_line("-r other.txt") is None
    assert requirement_name_from_line("PyYAML==6.0.2") == "PyYAML"


def test_add_to_empty_requirements_file():
    requirements = RequirementsFile("")
    updated, line_number, changed = requirements.add("Pillow")
    assert changed is True
    assert updated == "Pillow\n"
    assert line_number == 1
