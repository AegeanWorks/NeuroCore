"""Embedding-space identity and mismatch refusal (IDEA-591)."""

import pytest

from modules.knowledge_base.embedding_space import (
    EmbeddingSpace,
    EmbeddingSpaceMismatch,
    require_compatible,
    space_of,
)


def space(**overrides):
    values = {
        "model_id": "embed-v1",
        "revision": "2026-10",
        "dimension": 3,
        "normalization": "l2",
    }
    values.update(overrides)
    return EmbeddingSpace(**values)


def test_matching_spaces_are_compatible():
    require_compatible(space(), space())


def test_a_revision_mismatch_names_the_revision_field():
    with pytest.raises(EmbeddingSpaceMismatch) as caught:
        require_compatible(space(), space(revision="2026-11"))
    assert caught.value.field == "revision"
    assert "re-index" in str(caught.value)


def test_a_dimension_mismatch_names_the_dimension_field():
    with pytest.raises(EmbeddingSpaceMismatch) as caught:
        require_compatible(space(), space(dimension=4))
    assert caught.value.field == "dimension"


def test_a_vector_must_match_its_declared_dimension():
    space().validate_vector([1.0, 2.0, 3.0])
    with pytest.raises(EmbeddingSpaceMismatch):
        space().validate_vector([1.0, 2.0])


def test_space_of_reads_a_stored_record_and_treats_missing_or_bad_labels_as_legacy():
    stored = {"embedding_space": '{"model_id": "embed-v1", "revision": "r1", '
                                '"dimension": 3, "normalization": "l2"}'}
    assert space_of(stored) == EmbeddingSpace("embed-v1", "r1", 3, "l2")
    assert space_of({}) is None
    assert space_of({"embedding_space": "not-json"}) is None
    assert space_of({"embedding_space": '{"model_id": "", "revision": "r1", '
                                        '"dimension": 3}'}) is None


def test_from_settings_uses_the_embedding_model_and_defaults():
    value = EmbeddingSpace.from_settings(
        {"embedding_model": "embed-v2", "embedding_revision": "2026-10"}, dimension=8
    )
    assert value == EmbeddingSpace("embed-v2", "2026-10", 8, "l2")
    assert EmbeddingSpace.from_settings({}, dimension=0).model_id == "unconfigured"
