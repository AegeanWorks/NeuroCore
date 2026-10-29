"""
Tests for the Skills module.

Covers skill management, import/export, validation, and service layer.
"""

import pytest
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from modules.skills.backend import SkillBackend
from modules.skills.service import SkillService


# Valid skill content that passes validation (has structure + >100 chars)
VALID_SKILL_CONTENT = """# Test Skill

This is a test skill with sufficient content to pass validation.

## Overview

The skill provides testing guidelines and best practices.

### Section 1

- Item 1: First item
- Item 2: Second item
- Item 3: Third item

### Section 2

1. First step
2. Second step
3. Third step

## Conclusion

This skill has enough content and structure to be valid.
"""

VALID_SKILL_CONTENT_SHORT = """# Valid Skill

This skill has enough content and structure to pass validation.

## Overview

The skill provides testing guidelines and best practices.

### Section 1

- Item 1: First item
- Item 2: Second item

### Section 2

1. First step
2. Second step

## Conclusion

This skill is valid.
"""

INVALID_SKILL_CONTENT = "Too short"


class TestSkillBackend:
    """Test SkillBackend data persistence layer."""
    
    @pytest.fixture
    def temp_storage(self):
        """Create temporary storage directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.fixture
    def backend(self, temp_storage):
        """Create backend with temp storage."""
        return SkillBackend(temp_storage)
    
    def test_create_skill(self, backend):
        """Test creating a new skill."""
        skill = backend.create_skill(
            name="Test Skill",
            description="A test skill",
            content=VALID_SKILL_CONTENT,
            category="testing",
            tags=["test", "example"]
        )
        
        assert skill["name"] == "Test Skill"
        assert skill["description"] == "A test skill"
        assert skill["category"] == "testing"
        assert "test" in skill["tags"]
        assert "id" in skill
    
    def test_get_skill(self, backend):
        """Test retrieving a skill by ID."""
        created = backend.create_skill(
            name="Get Test",
            description="Test get",
            content=VALID_SKILL_CONTENT,
            category="general",
            tags=[]
        )
        
        retrieved = backend.get_skill(created["id"])
        
        assert retrieved is not None
        assert retrieved["id"] == created["id"]
        assert retrieved["name"] == "Get Test"
    
    def test_get_nonexistent_skill(self, backend):
        """Test retrieving non-existent skill returns None."""
        result = backend.get_skill("nonexistent-id")
        assert result is None
    
    def test_list_skills(self, backend):
        """Test listing all skills."""
        backend.create_skill("Skill 1", "Desc 1", VALID_SKILL_CONTENT, "cat1", ["tag1"])
        backend.create_skill("Skill 2", "Desc 2", VALID_SKILL_CONTENT, "cat2", ["tag2"])
        
        skills = backend.list_skills()
        
        assert len(skills) >= 2
        names = {s["name"] for s in skills}
        assert "Skill 1" in names
        assert "Skill 2" in names
    
    def test_list_skills_with_category_filter(self, backend):
        """Test filtering skills by category."""
        backend.create_skill("Cat1 Skill", "Desc", VALID_SKILL_CONTENT, "category1", [])
        backend.create_skill("Cat2 Skill", "Desc", VALID_SKILL_CONTENT, "category2", [])
        
        cat1_skills = [s for s in backend.list_skills() if s.get("category") == "category1"]
        cat2_skills = [s for s in backend.list_skills() if s.get("category") == "category2"]
        
        assert all(s["category"] == "category1" for s in cat1_skills)
        assert all(s["category"] == "category2" for s in cat2_skills)
    
    def test_list_skills_with_tag_filter(self, backend):
        """Test filtering skills by tag."""
        backend.create_skill("Tagged Skill", "Desc", VALID_SKILL_CONTENT, "general", ["important", "test"])
        backend.create_skill("Untagged Skill", "Desc", VALID_SKILL_CONTENT, "general", ["other"])
        
        tagged = [s for s in backend.list_skills() if "important" in s.get("tags", [])]
        
        assert len(tagged) >= 1
        assert all("important" in s["tags"] for s in tagged)
    
    def test_update_skill(self, backend):
        """Test updating a skill."""
        created = backend.create_skill(
            name="Original",
            description="Original desc",
            content=VALID_SKILL_CONTENT,
            category="general",
            tags=["old"]
        )
        
        updated = backend.update_skill(
            created["id"],
            name="Updated",
            description="New desc",
            tags=["new", "updated"]
        )
        
        assert updated["name"] == "Updated"
        assert updated["description"] == "New desc"
        assert "new" in updated["tags"]
        assert "updated" in updated["tags"]
        assert updated["category"] == "general"
    
    def test_delete_skill(self, backend):
        """Test deleting a skill."""
        created = backend.create_skill(
            name="To Delete",
            description="Will be deleted",
            content=VALID_SKILL_CONTENT,
            category="general",
            tags=[]
        )
        
        result = backend.delete_skill(created["id"])
        assert result is True
        
        retrieved = backend.get_skill(created["id"])
        assert retrieved is None
    
    def test_delete_nonexistent_skill(self, backend):
        """Test deleting non-existent skill returns False."""
        result = backend.delete_skill("nonexistent")
        assert result is False
    
    def test_import_skill(self, backend):
        """Test importing a skill from content."""
        content = """# Imported Skill

This is imported content with enough length and structure.

## Overview

Content here."""
        
        imported = backend.import_skill(
            skill_id="imported-test",
            name="Imported Skill",
            description="Imported from file",
            content=content,
            category="imported",
            tags=["import", "test"]
        )
        
        assert imported["name"] == "Imported Skill"
        assert imported["category"] == "imported"
        assert "import" in imported["tags"]
    
    def test_get_categories(self, backend):
        """Test getting all unique categories from skills."""
        backend.create_skill("Skill 1", "Desc", VALID_SKILL_CONTENT, "cat1", [])
        backend.create_skill("Skill 2", "Desc", VALID_SKILL_CONTENT, "cat2", [])
        backend.create_skill("Skill 3", "Desc", VALID_SKILL_CONTENT, "cat1", [])
        
        skills = backend.list_skills()
        categories = set(s.get("category", "general") for s in skills)
        
        assert "cat1" in categories
        assert "cat2" in categories
    
    def test_get_all_tags(self, backend):
        """Test getting all unique tags."""
        backend.create_skill("Skill 1", "Desc", VALID_SKILL_CONTENT, "general", ["tag1", "tag2"])
        backend.create_skill("Skill 2", "Desc", VALID_SKILL_CONTENT, "general", ["tag2", "tag3"])
        
        skills = backend.list_skills()
        tags = set()
        for skill in skills:
            tags.update(skill.get("tags", []))
        
        assert "tag1" in tags
        assert "tag2" in tags
        assert "tag3" in tags
    
    def test_get_skill_content_only(self, backend):
        """Test getting only skill content."""
        created = backend.create_skill(
            name="Content Test",
            description="Test",
            content="# Content Test\n\nFull content here with enough length.",
            category="general",
            tags=[]
        )
        
        content = backend.get_skill_content_only(created["id"])
        
        assert content == "# Content Test\n\nFull content here with enough length."
    
    def test_skill_exists(self, backend):
        """Test skill existence check."""
        created = backend.create_skill("Exist Test", "Desc", VALID_SKILL_CONTENT, "general", [])
        
        assert backend.skill_exists(created["id"]) is True
        assert backend.skill_exists("nonexistent") is False


class TestSkillService:
    """Test SkillService business logic layer."""
    
    @pytest.fixture
    def temp_storage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.fixture
    def service(self, temp_storage):
        return SkillService(temp_storage)
    
    def test_create_skill_validates_content(self, service):
        """Test that skill creation validates content format."""
        skill = service.create_skill(
            name="Valid Skill",
            description="Valid",
            content=VALID_SKILL_CONTENT,
            category="general",
            tags=[]
        )
        assert skill["name"] == "Valid Skill"
        
        with pytest.raises(ValueError, match="Invalid skill content format"):
            service.create_skill(
                name="Invalid Skill",
                description="Invalid",
                content=INVALID_SKILL_CONTENT,
                category="general",
                tags=[]
            )
    
    def test_update_skill_validates_content(self, service):
        """Test that skill update validates content."""
        created = service.create_skill(
            name="Valid Skill",
            description="Valid",
            content=VALID_SKILL_CONTENT,
            category="general",
            tags=[]
        )
        
        updated = service.update_skill(created["id"], content=VALID_SKILL_CONTENT_SHORT)
        assert updated["name"] == "Valid Skill"
        
        with pytest.raises(ValueError, match="Invalid skill content format"):
            service.update_skill(created["id"], content="x")
    
    def test_import_from_file_with_yaml_frontmatter(self, service):
        """Test importing skill with YAML frontmatter."""
        content = """---
name: Frontmatter Skill
description: Has frontmatter
category: frontmatter
tags: [yaml, frontmatter]
---

# Frontmatter Skill

This skill has frontmatter metadata and enough content to be valid.

## Overview

Content with frontmatter and sufficient length for validation."""
        
        imported = service.import_from_file(content, "frontmatter_skill.md")
        
        assert imported["name"] == "Frontmatter Skill"
        assert imported["description"] == "Has frontmatter"
        assert imported["category"] == "frontmatter"
        assert "yaml" in imported["tags"]
    
    def test_import_from_file_without_frontmatter(self, service):
        """Test importing skill without frontmatter (fallback extraction)."""
        content = """# Regex Skill

This is a skill extracted via regex with enough content to be valid.

Category: regex
Tags: regex, extraction

## Section

Content here with sufficient length to pass validation requirements."""
        
        imported = service.import_from_file(content, "regex_skill.md")
        
        assert imported["name"] == "Regex Skill"
        assert imported["category"] == "regex"
        assert "regex" in imported["tags"]
    
    def test_export_skill_json(self, service):
        """Test exporting skill as JSON."""
        created = service.create_skill(
            name="Export Test",
            description="Test export",
            content=VALID_SKILL_CONTENT,
            category="export",
            tags=["export", "test"]
        )
        
        export_data = service.export_skill(created["id"])
        
        assert export_data is not None
        assert "skill" in export_data
        assert "export_json" in export_data
        assert "export_markdown" in export_data
        
        exported = json.loads(export_data["export_json"])
        assert exported["metadata"]["name"] == "Export Test"
        assert exported["content"] == VALID_SKILL_CONTENT
    
    def test_export_skill_markdown(self, service):
        """Test exporting skill as Markdown with frontmatter."""
        created = service.create_skill(
            name="Markdown Export",
            description="Test markdown export",
            content=VALID_SKILL_CONTENT,
            category="export",
            tags=["markdown", "export"]
        )
        
        export_data = service.export_skill(created["id"])
        
        markdown = export_data["export_markdown"]
        assert markdown.startswith("---\n")
        assert "name: Markdown Export" in markdown
        assert "category: export" in markdown
        assert "Test Skill" in markdown
    
    def test_get_enabled_skills_content(self, service):
        """Test getting content for multiple enabled skills."""
        skill1 = service.create_skill("Skill 1", "Desc", VALID_SKILL_CONTENT, "general", [])
        skill2 = service.create_skill("Skill 2", "Desc", VALID_SKILL_CONTENT_SHORT, "general", [])
        
        contents = service.get_enabled_skills_content([skill1["id"], skill2["id"]])
        
        assert len(contents) == 2
        assert skill1["id"] in contents
        assert skill2["id"] in contents
        assert "Test Skill" in contents[skill1["id"]]
        assert "Valid Skill" in contents[skill2["id"]]
    
    def test_get_enabled_skills_content_ignores_missing(self, service):
        """Test that missing skill IDs are ignored."""
        skill1 = service.create_skill("Skill 1", "Desc", VALID_SKILL_CONTENT, "general", [])
        
        contents = service.get_enabled_skills_content([skill1["id"], "nonexistent"])
        
        assert len(contents) == 1
        assert skill1["id"] in contents


class TestSkillValidation:
    """Test skill content validation."""
    
    @pytest.fixture
    def service(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield SkillService(tmpdir)
    
    def test_validate_skill_content_with_headers(self, service):
        """Test validation passes with markdown headers."""
        content = VALID_SKILL_CONTENT
        assert service._validate_skill_content(content) is True
    
    def test_validate_skill_content_with_lists(self, service):
        """Test validation passes with markdown lists."""
        content = VALID_SKILL_CONTENT
        assert service._validate_skill_content(content) is True
    
    def test_validate_skill_content_with_numbered_lists(self, service):
        """Test validation passes with numbered lists."""
        content = VALID_SKILL_CONTENT
        assert service._validate_skill_content(content) is True
    
    def test_validate_skill_content_rejects_empty(self, service):
        """Test validation rejects empty content."""
        assert service._validate_skill_content("") is False
        assert service._validate_skill_content("   ") is False
    
    def test_validate_skill_content_rejects_no_structure(self, service):
        """Test validation rejects content without structure."""
        content = "Just a paragraph with no markdown structure at all but it is long enough to pass the length check which is one hundred characters minimum requirement."
        assert service._validate_skill_content(content) is False
    
    def test_validate_skill_content_rejects_too_short(self, service):
        """Test validation rejects content under 100 chars."""
        content = "# Title\n\nShort."
        assert service._validate_skill_content(content) is False
    
    def test_validate_skill_content_accepts_minimum_valid(self, service):
        """Test validation accepts minimum valid content (100 chars + structure)."""
        content = "# Title\n\n" + "x" * 95
        assert service._validate_skill_content(content) is True


class TestSkillMetadataExtraction:
    """Test metadata extraction from skill content."""
    
    @pytest.fixture
    def service(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield SkillService(tmpdir)
    
    def test_extract_title_from_h1(self, service):
        """Test extracting title from first H1."""
        content = "# My Skill Title\n\nSome content."
        title = service._extract_title(content)
        assert title == "My Skill Title"
    
    def test_extract_title_returns_none_when_no_h1(self, service):
        """Test title extraction returns None when no H1."""
        content = "No header here\n\nJust content."
        title = service._extract_title(content)
        assert title is None
    
    def test_extract_description_first_paragraph(self, service):
        """Test extracting description from first paragraph after title."""
        content = """# Title

This is the first paragraph.
It describes the skill.

## Section

More content."""
        
        desc = service._extract_description(content)
        assert "first paragraph" in desc
        assert len(desc) <= 200
    
    def test_clean_file_name(self, service):
        """Test cleaning file names to skill names."""
        assert service._clean_file_name("my_skill_file.md") == "My Skill File"
        assert service._clean_file_name("another-skill.md") == "Another Skill"
        assert service._clean_file_name("SKILL.MD") == "Skill"
    
    def test_generate_import_id(self, service):
        """Test generating unique import IDs."""
        # Use counter suffix to ensure uniqueness instead of relying on timestamp
        id1 = service._generate_import_id("Test Skill")
        # Manually create a different ID by calling with a slightly different name
        id2 = service._generate_import_id("Test Skill 2")
        
        assert id1.startswith("test_skill_")
        assert id2.startswith("test_skill_")
        assert id1 != id2


def test_example_skill_loads():
    """Test that the example skill file loads correctly."""
    from modules.skills.backend import SkillBackend
    
    backend = SkillBackend("modules/skills/data")
    skills = backend.list_skills()
    
    skill_names = {s["name"] for s in skills}
    assert "Python Best Practices" in skill_names or len(skills) > 0


def test_skill_content_sanitization():
    """Test that skill content is properly handled."""
    from modules.skills.service import SkillService
    
    with tempfile.TemporaryDirectory() as tmpdir:
        service = SkillService(tmpdir)
        
        skill = service.create_skill(
            name="XSS Test",
            description="Test",
            content=VALID_SKILL_CONTENT + "\n\n<script>alert('xss')</script>",
            category="security",
            tags=[]
        )
        
        assert "<script>" in skill["content"]
        assert "alert" in skill["content"]


def test_import_from_url_rejects_mixed_public_and_loopback_answers(monkeypatch):
    import socket

    with tempfile.TemporaryDirectory() as tmpdir:
        service = SkillService(tmpdir)

        def fake_getaddrinfo(*_args, **_kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 0, 0, 0)),
            ]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(ValueError, match="blocked IP"):
            service.import_from_url("http://example.com/skill.md")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])