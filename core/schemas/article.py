"""
Article Schema

Pydantic model for representing academic articles.
"""

import uuid
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime


ArticleType = Literal["research", "review", "meta_analysis", "case_study", "commentary"]
ArticleStatus = Literal["active", "archived", "deleted"]


class Article(BaseModel):
    """
    Represents an academic article.
    
    Stores metadata and content from scholarly articles including
    bibliographic information, abstract, and key findings.
    """
    
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the article"
    )
    
    # Bibliographic info
    title: str = Field(
        ...,
        description="Article title",
        min_length=3,
        max_length=500
    )
    
    authors: List[str] = Field(
        default_factory=list,
        description="List of author names"
    )
    
    year: Optional[int] = Field(
        default=None,
        description="Publication year",
        ge=1900,
        le=2100
    )
    
    journal: Optional[str] = Field(
        default=None,
        description="Journal or publication venue"
    )
    
    volume: Optional[str] = Field(
        default=None,
        description="Journal volume"
    )
    
    issue: Optional[str] = Field(
        default=None,
        description="Journal issue"
    )
    
    pages: Optional[str] = Field(
        default=None,
        description="Page range (e.g., '1-15')"
    )
    
    doi: Optional[str] = Field(
        default=None,
        description="Digital Object Identifier"
    )
    
    url: Optional[str] = Field(
        default=None,
        description="Article URL"
    )
    
    # Content
    abstract: Optional[str] = Field(
        default=None,
        description="Article abstract"
    )
    
    keywords: List[str] = Field(
        default_factory=list,
        description="Article keywords"
    )
    
    # Classification
    article_type: ArticleType = Field(
        default="research",
        description="Type: research, review, meta_analysis, case_study, commentary"
    )
    
    domain: str = Field(
        default="general",
        description="Scientific domain or field"
    )
    
    subdomains: List[str] = Field(
        default_factory=list,
        description="Specific subdomains"
    )
    
    # Content summary
    methodology: Optional[str] = Field(
        default=None,
        description="Research methodology used"
    )
    
    key_findings: List[str] = Field(
        default_factory=list,
        description="Key findings from the article"
    )
    
    conclusions: List[str] = Field(
        default_factory=list,
        description="Main conclusions"
    )
    
    # Links
    related_hypotheses: List[str] = Field(
        default_factory=list,
        description="Related hypothesis IDs"
    )
    
    related_studies: List[str] = Field(
        default_factory=list,
        description="Related study design IDs"
    )
    
    related_findings: List[str] = Field(
        default_factory=list,
        description="Related finding IDs"
    )
    
    citations: List[str] = Field(
        default_factory=list,
        description="Cited article DOIs"
    )
    
    # Metadata
    source: str = Field(
        default="manual",
        description="Source: manual, arxiv, pubmed, semantic_scholar, etc."
    )
    
    added_by: Optional[str] = Field(
        default=None,
        description="User who added the article"
    )
    
    # Status
    status: ArticleStatus = Field(
        default="active",
        description="Status: active, archived, deleted"
    )
    
    verified: bool = Field(
        default=False,
        description="Whether information has been verified"
    )
    
    created_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when article was added"
    )
    
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when article was last updated"
    )
    
    accessed_at: Optional[datetime] = Field(
        default=None,
        description="Last time article was accessed"
    )
    
    notes: Optional[str] = Field(
        default=None,
        description="Additional notes"
    )
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "Sleep and Cognitive Performance: A Meta-Analysis",
                "authors": ["Smith, J.", "Doe, A."],
                "year": 2023,
                "journal": "Journal of Sleep Research",
                "doi": "10.1111/jsr.14000",
                "article_type": "meta_analysis",
                "domain": "cognitive_science"
            }
        }
    }

