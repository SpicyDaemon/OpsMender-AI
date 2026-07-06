from backend.db.migrations.versions.f6a7b8c9d0e1_normalize_memory_severity_tags import (
    _normalize_tags as normalize_migration_tags,
)
from backend.memory.tags import normalize_memory_tags


def test_normalize_memory_tags_canonicalizes_severity_and_dedupes():
    assert normalize_memory_tags(
        [" High ", "severity-high", "critical", "", 42, "payments"]
    ) == ["severity-high", "severity-critical", "payments"]


def test_migration_normalization_is_idempotent():
    first = normalize_migration_tags(
        ["high", "severity-high", "medium", "payments", "payments"]
    )
    assert first == ["severity-high", "severity-medium", "payments"]
    assert normalize_migration_tags(first) == first
