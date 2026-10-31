"""
Scientific Schemas for NeuroCore

This package contains Pydantic models for structured data in the scientific system:
- Hypothesis: For hypothesis data
- StudyDesign: For study design specifications
- Finding: For research findings
- Article: For article metadata
"""

from core.schemas.hypothesis import Hypothesis
from core.schemas.study_design import StudyDesign
from core.schemas.finding import Finding
from core.schemas.article import Article

__all__ = [
    "Hypothesis",
    "StudyDesign",
    "Finding",
    "Article",
]

