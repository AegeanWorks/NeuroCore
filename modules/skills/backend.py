"""
Backend for Skills module - handles file storage and retrieval of SKILL.md files.
"""

import os
import json
import re
from datetime import datetime
from typing import Optional, List, Dict, Any

from .versions import SkillVersionStore


class SkillBackend:
    """Handles file-based storage for skills."""
    
    def __init__(self, storage_path: str = None):
        # Use absolute path based on this file's location for consistency
        if storage_path is None:
            storage_path = os.path.join(os.path.dirname(__file__), "data")
        self.storage_path = storage_path
        self.metadata_file = os.path.join(storage_path, "skills_metadata.json")
        self.versions = SkillVersionStore(storage_path)
        self._ensure_storage_exists()
    
    def _ensure_storage_exists(self):
        """Ensure storage directory and metadata file exist."""
        os.makedirs(self.storage_path, exist_ok=True)
        if not os.path.exists(self.metadata_file):
            self._save_metadata({})
    
    def _save_metadata(self, metadata: Dict[str, Any]):
        """Save skills metadata to JSON file."""
        with open(self.metadata_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
    
    def _load_metadata(self) -> Dict[str, Any]:
        """Load skills metadata from JSON file."""
        try:
            with open(self.metadata_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}
    
    def _get_skill_path(self, skill_id: str) -> str:
        """Get file path for a skill's markdown file; reject anything that can escape it."""
        name = str(skill_id)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", name):
            raise ValueError(
                "skill_id must be 1-128 characters of letters, digits, underscore or hyphen"
            )
        return os.path.join(self.storage_path, f"{name}.md")
    
    def _generate_skill_id(self, name: str) -> str:
        """Generate a URL-friendly skill ID from name."""
        import time
        # Convert to lowercase, replace spaces with underscores, remove special chars
        skill_id = re.sub(r'[^a-zA-Z0-9\s]', '', name.lower())
        skill_id = re.sub(r'\s+', '_', skill_id)
        
        # Ensure uniqueness with timestamp suffix for more robust generation
        metadata = self._load_metadata()
        base_id = skill_id
        
        # First check if base_id exists
        if skill_id not in metadata:
            return skill_id
        
        # If exists, try with timestamp first (most robust)
        timestamp = int(time.time())
        skill_id = f"{base_id}_{timestamp}"
        if skill_id not in metadata:
            return skill_id
        
        # Fall back to counter if timestamp collision occurs (rare)
        counter = 1
        while skill_id in metadata:
            skill_id = f"{base_id}_{timestamp}_{counter}"
            counter += 1
        return skill_id
    
    def list_skills(self) -> List[Dict[str, Any]]:
        """List all skills with their metadata."""
        metadata = self._load_metadata()
        skills = []
        
        for skill_id, meta in metadata.items():
            skill_path = self._get_skill_path(skill_id)
            if os.path.exists(skill_path):
                # Add file stats
                stat = os.stat(skill_path)
                meta["id"] = skill_id
                meta["file_size"] = stat.st_size
                meta["modified_at"] = datetime.fromtimestamp(stat.st_mtime).isoformat()
                skills.append(meta)
        
        return sorted(skills, key=lambda x: x.get("created_at", ""), reverse=True)
    
    def get_skill(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """Get a skill's metadata and content."""
        metadata = self._load_metadata()
        
        if skill_id not in metadata:
            return None
        
        skill_path = self._get_skill_path(skill_id)
        if not os.path.exists(skill_path):
            return None
        
        with open(skill_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        meta = metadata[skill_id]
        meta["id"] = skill_id
        meta["content"] = content
        
        return meta
    
    def create_skill(self, name: str, description: str, content: str, 
                     category: str = "general", tags: List[str] = None) -> Dict[str, Any]:
        """Create a new skill."""
        tags = tags or []
        skill_id = self._generate_skill_id(name)
        skill_path = self._get_skill_path(skill_id)
        
        # Save content
        with open(skill_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        # Update metadata
        metadata = self._load_metadata()
        metadata[skill_id] = {
            "name": name,
            "description": description,
            "category": category,
            "tags": tags,
            "created_at": datetime.utcnow().isoformat() + 'Z',
            "updated_at": datetime.utcnow().isoformat() + 'Z'
        }
        self._save_metadata(metadata)
        self.versions.record(skill_id, content, note="created")
        
        return {
            "id": skill_id,
            "name": name,
            "description": description,
            "category": category,
            "tags": tags,
            "content": content
        }
    
    def update_skill(self, skill_id: str, name: Optional[str] = None,
                     description: Optional[str] = None, content: Optional[str] = None,
                     category: Optional[str] = None, tags: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """Update an existing skill."""
        metadata = self._load_metadata()
        
        if skill_id not in metadata:
            return None
        
        # Update content if provided
        if content is not None:
            previous = self.get_skill_content_only(skill_id)
            skill_path = self._get_skill_path(skill_id)
            with open(skill_path, "w", encoding="utf-8") as f:
                f.write(content)
            if content != previous:
                self.versions.record(skill_id, content, note="updated")
        
        # Update metadata
        meta = metadata[skill_id]
        if name is not None:
            meta["name"] = name
        if description is not None:
            meta["description"] = description
        if category is not None:
            meta["category"] = category
        if tags is not None:
            meta["tags"] = tags
        
        meta["updated_at"] = datetime.utcnow().isoformat() + 'Z'
        self._save_metadata(metadata)
        
        return self.get_skill(skill_id)
    
    def delete_skill(self, skill_id: str) -> bool:
        """Delete a skill."""
        metadata = self._load_metadata()
        
        if skill_id not in metadata:
            return False
        
        # Remove files
        skill_path = self._get_skill_path(skill_id)
        if os.path.exists(skill_path):
            os.remove(skill_path)
        
        # Update metadata
        del metadata[skill_id]
        self._save_metadata(metadata)
        
        return True
    
    def import_skill(self, skill_id: str, name: str, description: str, 
                     content: str, category: str = "general", 
                     tags: List[str] = None) -> Dict[str, Any]:
        """Import a skill with a specific ID (used for import/export)."""
        tags = tags or []
        skill_path = self._get_skill_path(skill_id)
        
        # Save content
        with open(skill_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        # Update metadata
        metadata = self._load_metadata()
        metadata[skill_id] = {
            "name": name,
            "description": description,
            "category": category,
            "tags": tags,
            "created_at": datetime.utcnow().isoformat() + 'Z',
            "updated_at": datetime.utcnow().isoformat() + 'Z',
            "imported": True
        }
        self._save_metadata(metadata)
        self.versions.record(skill_id, content, note="imported")
        
        return {
            "id": skill_id,
            "name": name,
            "description": description,
            "category": category,
            "tags": tags,
            "content": content
        }
    
    def get_skill_content_only(self, skill_id: str) -> Optional[str]:
        """Get only the content of a skill (for system prompt injection)."""
        skill_path = self._get_skill_path(skill_id)
        
        if not os.path.exists(skill_path):
            return None
        
        with open(skill_path, "r", encoding="utf-8") as f:
            return f.read()
    
    def skill_exists(self, skill_id: str) -> bool:
        """Check if a skill exists."""
        metadata = self._load_metadata()
        skill_path = self._get_skill_path(skill_id)
        return skill_id in metadata and os.path.exists(skill_path)

    def list_versions(self, skill_id: str) -> List[Dict[str, Any]]:
        """Version history for one skill, oldest first."""
        if not self.skill_exists(skill_id):
            return []
        return self.versions.versions(skill_id)

    def get_version(self, skill_id: str, version: int) -> Optional[Dict[str, Any]]:
        """One version, including its content."""
        if not self.skill_exists(skill_id):
            return None
        return self.versions.read(skill_id, version)

    def rollback(self, skill_id: str, version: int) -> Optional[Dict[str, Any]]:
        """Restore a version's content; the restore is itself recorded as a new version."""
        record = self.get_version(skill_id, version)
        if record is None:
            return None
        return self.update_skill(skill_id, content=record["content"])
