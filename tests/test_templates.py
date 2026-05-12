"""Integrity tests for the messages.yaml template pool."""

from src.main import load_templates


def test_load_templates_returns_non_empty_list():
    templates = load_templates()
    assert isinstance(templates, list)
    assert len(templates) >= 5, "expected a healthy rotation pool"


def test_every_template_uses_both_placeholders():
    for i, template in enumerate(load_templates()):
        assert "{code}" in template, f"template #{i} missing {{code}}"
        assert "{link}" in template, f"template #{i} missing {{link}}"


def test_every_template_formats_without_error():
    code = "TESTCODE"
    link = "https://www.visible.com/get/?TESTCODE"
    for i, template in enumerate(load_templates()):
        rendered = template.format(code=code, link=link)
        assert code in rendered, f"template #{i} did not substitute code"
        assert link in rendered, f"template #{i} did not substitute link"
        assert "{code}" not in rendered, f"template #{i} left {{code}} unsubstituted"
        assert "{link}" not in rendered, f"template #{i} left {{link}} unsubstituted"
        assert rendered.strip(), f"template #{i} renders empty"


def test_templates_are_distinct():
    templates = load_templates()
    assert len(set(templates)) == len(templates), "duplicate template in pool"
