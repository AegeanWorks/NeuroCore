"""
Hypothesis Schema

Pydantic model for representing scientific hypotheses.
"""

import uuid
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime


HypothesisType = Literal["correlation", "causal", "directional", "null"]
HypothesisStatus = Literal["proposed", "tested", "supported", "rejected", "modified"]


class Hypothesis(BaseModel):
    """
    Represents a scientific hypothesis.
    
    A hypothesis is a proposed explanation for a phenomenon, which can be
    tested through experimentation or observation.
    """
    
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the hypothesis"
    )
    
    title: str = Field(
        ...,
        description="Brief title of the hypothesis",
        min_length=3,
        max_length=200
    )
    
    statement: str = Field(
        ...,
        description="The core hypothesis statement",
        min_length=10,
        max_length=2000
    )
    
    variables: List[str] = Field(
        default_factory=list,
        description="List of variables involved in the hypothesis"
    )
    
    independent_variable: Optional[str] = Field(
        default=None,
        description="The independent variable (what is manipulated)"
    )
    
    dependent_variable: Optional[str] = Field(
        default=None,
        description="The dependent variable (what is measured)"
    )
    
    hypothesis_type: HypothesisType = Field(
        default="correlation",
        description="Type: correlation, causal, directional, null"
    )
    
    confidence_level: float = Field(
        default=0.95,
        description="Statistical confidence level (0-1)",
        ge=0.0,
        le=1.0
    )
    
    domain: str = Field(
        default="general",
        description="Scientific domain or field"
    )
    
    status: HypothesisStatus = Field(
        default="proposed",
        description="Status: proposed, tested, supported, rejected, modified"
    )
    
    evidence: List[str] = Field(
        default_factory=list,
        description="Supporting evidence or references"
    )
    
    created_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when hypothesis was created"
    )
    
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when hypothesis was last updated"
    )
    
    notes: Optional[str] = Field(
        default=None,
        description="Additional notes or comments"
    )
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "Effect of Sleep on Memory",
                "statement": "Students who sleep for 8 hours before an exam will perform better than those who sleep for 4 hours.",
                "variables": ["sleep_duration", "exam_score"],
                "independent_variable": "sleep_duration",
                "dependent_variable": "exam_score",
                "hypothesis_type": "causal",
                "domain": "cognitive_science"
            }
        }
    }

