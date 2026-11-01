"""Embedding-space identity for one collection (IDEA-591).

Vectors from different models or dimensions are not comparable. A collection stamps its
``(model_id, revision, dimension, normalization)`` once and refuses a query or an ingestion
from a different space instead of silently returning meaningless neighbours.
"""

import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Literal, Mapping, Optional

FIELDS = ("model_id", "revision", "dimension", "normalization")


class EmbeddingSpaceMismatch(ValueError):
    """A vector, record or query belongs to a different embedding space."""

    def __init__(self, field: str, index_value: Any, query_value: Any):
        self.field = field
        self.index_value = index_value
        self.query_value = query_value
        super().__init__(
            f"Embedding space mismatch on {field}: index={index_value!r}, "
            f"query={query_value!r}. Why: vectors from different spaces are not comparable. "
            "Fix: re-index the collection with the current embedding model, or point the query "
            "at the model that produced the index."
        )


@dataclass(frozen=True)
class EmbeddingSpace:
    """The identity of the vectors in one collection."""

    model_id: str
    revision: str
    dimension: int
    normalization: Literal["l2", "none"] = "l2"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EmbeddingSpace":
        if not isinstance(value, Mapping):
            raise ValueError("embedding space must be a mapping")
        model_id = str(value.get("model_id") or "").strip()
        revision = str(value.get("revision") or "").strip()
        dimension = value.get("dimension")
        normalization = str(value.get("normalization") or "l2")
        if not model_id or not revision:
            raise ValueError("embedding space needs model_id and revision")
        if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
            raise ValueError("embedding space dimension must be a positive integer")
        if normalization not in ("l2", "none"):
            raise ValueError("embedding space normalization must be 'l2' or 'none'")
        return cls(model_id, revision, dimension, normalization)

    @classmethod
    def from_settings(cls, values: Optional[Mapping[str, Any]], dimension: int = 0) -> "EmbeddingSpace":
        values = values or {}
        model_id = str(values.get("embedding_model") or "unconfigured").strip() or "unconfigured"
        revision = str(values.get("embedding_revision") or "local").strip() or "local"
        normalization = str(values.get("embedding_normalization") or "l2")
        return cls(model_id, revision, dimension, normalization)  # type: ignore[arg-type]

    def with_dimension(self, dimension: int) -> "EmbeddingSpace":
        return EmbeddingSpace(self.model_id, self.revision, dimension, self.normalization)

    def validate_vector(self, vector: Any, record_id: Any = None) -> None:
        """Refuse a vector whose length disagrees with the declared dimension."""
        try:
            length = len(vector)
        except TypeError as exc:
            raise EmbeddingSpaceMismatch("dimension", self.dimension, None) from exc
        if length != self.dimension:
            label = f"record {record_id}: " if record_id is not None else ""
            raise EmbeddingSpaceMismatch(f"dimension ({label}vector length)", self.dimension, length)


def space_of(record: Any) -> Optional[EmbeddingSpace]:
    """The declared space of one stored record; ``None`` means a legacy, unlabelled record."""
    if not isinstance(record, Mapping):
        return None
    raw = record.get("embedding_space")
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    try:
        return EmbeddingSpace.from_dict(raw)
    except (TypeError, ValueError):
        return None


def require_compatible(index_space: EmbeddingSpace, query_space: EmbeddingSpace) -> None:
    """Raise on the first field that differs, naming the field and both values."""
    for field in FIELDS:
        index_value = getattr(index_space, field)
        query_value = getattr(query_space, field)
        if index_value != query_value:
            raise EmbeddingSpaceMismatch(field, index_value, query_value)
