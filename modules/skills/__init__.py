"""
Skills module for NeuroCore - manages instruction files (SKILL.md) containing 
best practices, patterns, and guidelines for AI tasks.
"""

from .router import router
from .service import SkillService
from .backend import SkillBackend

__all__ = ["router", "SkillService", "SkillBackend"]
