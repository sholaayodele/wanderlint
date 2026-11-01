from pathlib import Path

import yaml

from wanderlint.models import Taxonomy

ROOT = Path(__file__).resolve().parents[1]

# Expected (severity, detection) per defect, from CLAUDE.md §3.
EXPECTED = {
    "D1": ("critical", "rule"),
    "D2": ("critical", "llm"),
    "D3": ("critical", "llm"),
    "D4": ("critical", "llm"),
    "D5": ("major", "both"),
    "D6": ("critical", "both"),
    "D7": ("major", "llm"),
    "D8": ("minor", "rule"),
}


def test_taxonomy_loads_and_is_valid() -> None:
    raw = yaml.safe_load((ROOT / "taxonomy.yaml").read_text(encoding="utf-8"))
    taxonomy = Taxonomy.model_validate(raw)

    assert taxonomy.version == 1

    ids = [d.id for d in taxonomy.defects]
    assert ids == sorted(EXPECTED), f"unexpected or duplicate ids: {ids}"

    locales: set[str] = set()
    for d in taxonomy.defects:
        assert (d.severity, d.detection) == EXPECTED[d.id], d.id
        assert len(d.decision_rules) >= 1, d.id
        assert all(r.strip() for r in d.decision_rules), d.id
        assert len(d.examples.positive) == 2, d.id
        assert len(d.examples.negative) == 1, d.id
        for ex in d.examples.positive + d.examples.negative:
            assert ex.text.strip(), d.id
            locales.add(ex.locale)

    assert locales == {"en-GB", "de-DE"}

    d1 = taxonomy.by_id()["D1"]
    assert d1.required_fields == [
        "title",
        "short_description",
        "description",
        "duration",
        "meeting_point",
        "inclusions",
        "cancellation_policy",
        "category",
        "locale",
    ]
