"""
Study Design Schema

Pydantic model for representing research study designs.
"""

import uuid
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime


StudyDesignStatus = Literal["planned", "approved", "recruiting", "active", "completed", "terminated"]


class StudyDesign(BaseModel):
    """
    Represents a research study design.
    
    A study design outlines how research will be conducted, including
    methodology, participants, procedures, and analysis plans.
    """
    
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the study design"
    )
    
    title: str = Field(
        ...,
        description="Title of the study",
        min_length=3,
        max_length=300
    )
    
    study_type: Literal["experimental", "observational", "quasi-experimental", "correlational", "case_study", "longitudinal", "cross-sectional", "meta_analysis"] = Field(
        ...,
        description="Type of study: experimental, observational, quasi-experimental, etc."
    )
    
    hypothesis_id: Optional[str] = Field(
        default=None,
        description="Associated hypothesis ID"
    )
    
    # Participants
    population: str = Field(
        ...,
        description="Target population description"
    )
    
    sample_size: Optional[int] = Field(
        default=None,
        description="Target sample size",
        ge=1
    )
    
    sampling_method: str = Field(
        default="random",
        description="Sampling method: random, convenience, stratified, cluster, etc."
    )
    
    inclusion_criteria: List[str] = Field(
        default_factory=list,
        description="Participant inclusion criteria"
    )
    
    exclusion_criteria: List[str] = Field(
        default_factory=list,
        description="Participant exclusion criteria"
    )
    
    # Variables
    variables: Dict[str, str] = Field(
        default_factory=dict,
        description="Variables and their types (independent, dependent, control)"
    )
    
    # Design specifics
    design_type: Optional[str] = Field(
        default=None,
        description="Specific design: between-subjects, within-subjects, mixed, etc."
    )
    
    control_group: bool = Field(
        default=False,
        description="Whether study includes a control group"
    )
    
    blinding: Optional[str] = Field(
        default=None,
        description="Blinding type: single, double, none"
    )
    
    randomization: bool = Field(
        default=False,
        description="Whether participants are randomly assigned"
    )
    
    # Procedures
    procedures: List[str] = Field(
        default_factory=list,
        description="Study procedures/steps"
    )
    
    duration: Optional[str] = Field(
        default=None,
        description="Expected study duration"
    )
    
    measurement_instruments: List[str] = Field(
        default_factory=list,
        description="Instruments used for measurement"
    )
    
    # Analysis
    analysis_plan: List[str] = Field(
        default_factory=list,
        description="Planned statistical analyses"
    )
    
    # Ethical considerations
    ethics_approval: bool = Field(
        default=False,
        description="Whether ethics approval has been obtained"
    )
    
    informed_consent: bool = Field(
        default=True,
        description="Whether informed consent is required"
    )
    
    # Status
    status: StudyDesignStatus = Field(
        default="planned",
        description="Status: planned, approved, recruiting, active, completed, terminated"
    )
    
    created_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when study design was created"
    )
    
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when study design was last updated"
    )
    
    notes: Optional[str] = Field(
        default=None,
        description="Additional notes"
    )
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "Sleep and Memory Study",
                "study_type": "experimental",
                "population": "University students aged 18-25",
                "sample_size": 100,
                "sampling_method": "random",
                "design_type": "between-subjects",
                "control_group": True,
                "randomization": True,
                "blinding": "double"
            }
        }
    }

