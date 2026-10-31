"""
Finding Schema

Pydantic model for representing research findings.
"""

import uuid
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime


FindingSignificance = Literal["significant", "not_significant", "marginal"]
FindingStatus = Literal["preliminary", "peer_reviewed", "published", "retracted"]


class Finding(BaseModel):
    """
    Represents a research finding.
    
    A finding documents the results of a study or experiment,
    including statistical results, conclusions, and implications.
    """
    
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the finding"
    )
    
    study_id: Optional[str] = Field(
        default=None,
        description="Associated study design ID"
    )
    
    hypothesis_id: Optional[str] = Field(
        default=None,
        description="Associated hypothesis ID"
    )
    
    title: str = Field(
        ...,
        description="Brief title of the finding",
        min_length=3,
        max_length=300
    )
    
    summary: str = Field(
        ...,
        description="Summary of the finding",
        min_length=10,
        max_length=2000
    )
    
    # Statistical results
    statistical_test: Optional[str] = Field(
        default=None,
        description="Statistical test used (t-test, ANOVA, chi-square, etc.)"
    )
    
    test_statistic: Optional[float] = Field(
        default=None,
        description="Test statistic value"
    )
    
    degrees_of_freedom: Optional[float] = Field(
        default=None,
        description="Degrees of freedom"
    )
    
    p_value: Optional[float] = Field(
        default=None,
        description="P-value",
        ge=0.0,
        le=1.0
    )
    
    effect_size: Optional[float] = Field(
        default=None,
        description="Effect size measure"
    )
    
    confidence_interval: Optional[str] = Field(
        default=None,
        description="Confidence interval (e.g., '95% CI [1.2, 3.4]')"
    )
    
    # Additional results
    sample_size: Optional[int] = Field(
        default=None,
        description="Actual sample size in analysis",
        ge=1
    )
    
    results: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional numerical results"
    )
    
    # Conclusion
    conclusion: str = Field(
        ...,
        description="Research conclusion",
        min_length=5,
        max_length=2000
    )
    
    significance: FindingSignificance = Field(
        default="not_significant",
        description="Statistical significance: significant, not_significant, marginal"
    )
    
    # Interpretation
    interpretation: Optional[str] = Field(
        default=None,
        description="Interpretation of the results"
    )
    
    limitations: List[str] = Field(
        default_factory=list,
        description="Study limitations"
    )
    
    implications: List[str] = Field(
        default_factory=list,
        description="Research implications"
    )
    
    # Supporting materials
    data_available: bool = Field(
        default=False,
        description="Whether raw data is available"
    )
    
    code_available: bool = Field(
        default=False,
        description="Whether analysis code is available"
    )
    
    # Status
    status: FindingStatus = Field(
        default="preliminary",
        description="Status: preliminary, peer_reviewed, published, retracted"
    )
    
    peer_reviewed: bool = Field(
        default=False,
        description="Whether finding has been peer-reviewed"
    )
    
    created_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when finding was recorded"
    )
    
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when finding was last updated"
    )
    
    notes: Optional[str] = Field(
        default=None,
        description="Additional notes"
    )
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "Sleep Duration Effect on Exam Performance",
                "summary": "Students with 8 hours of sleep scored significantly higher than those with 4 hours.",
                "statistical_test": "independent t-test",
                "test_statistic": 3.45,
                "p_value": 0.001,
                "effect_size": 0.72,
                "significance": "significant",
                "conclusion": "Adequate sleep significantly improves exam performance."
            }
        }
    }

