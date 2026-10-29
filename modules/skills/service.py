"""
Service layer for Skills module - business logic for skill management.
"""

import logging
import json
import re
import httpx
from typing import List, Dict, Any, Optional
from .backend import SkillBackend

logger = logging.getLogger(__name__)


class SkillService:
    """Business logic for skill management."""
    
    def __init__(self, storage_path: str = "modules/skills/data"):
        self.backend = SkillBackend(storage_path)
    
    def list_skills(self, category: Optional[str] = None, 
                    tag: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all skills with optional filtering."""
        skills = self.backend.list_skills()
        
        if category:
            skills = [s for s in skills if s.get("category") == category]
        
        if tag:
            skills = [s for s in skills if tag in s.get("tags", [])]
        
        return skills
    
    def get_skill(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """Get a skill by ID."""
        return self.backend.get_skill(skill_id)
    
    def create_skill(self, name: str, description: str, content: str,
                     category: str = "general", tags: List[str] = None) -> Dict[str, Any]:
        """Create a new skill."""
        # Validate content format
        if not self._validate_skill_content(content):
            raise ValueError("Invalid skill content format")
        
        return self.backend.create_skill(name, description, content, category, tags)
    
    def update_skill(self, skill_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        """Update an existing skill."""
        if "content" in kwargs and not self._validate_skill_content(kwargs["content"]):
            raise ValueError("Invalid skill content format")
        
        return self.backend.update_skill(skill_id, **kwargs)
    
    def delete_skill(self, skill_id: str) -> bool:
        """Delete a skill."""
        return self.backend.delete_skill(skill_id)
    
    def import_from_file(self, file_content: str, file_name: str = "imported_skill.md") -> Dict[str, Any]:
        """Import a skill from file content."""
        # Try to parse metadata from the file
        metadata = self._extract_metadata_from_content(file_content, file_name)
        
        # Generate skill ID from name
        skill_id = self._generate_import_id(metadata["name"])
        
        return self.backend.import_skill(
            skill_id=skill_id,
            name=metadata["name"],
            description=metadata["description"],
            content=file_content,
            category=metadata.get("category", "general"),
            tags=metadata.get("tags", [])
        )
    
    def import_from_url(self, url: str) -> Dict[str, Any]:
        """Import a skill from a URL with SSRF protection."""
        import socket
        import re
        from urllib.parse import urlparse
        
        # SSRF protection: Validate URL before making request
        # Blocked IP patterns (private/internal networks)
        blocked_ip_patterns = [
            r'^127\.',  # Loopback
            r'^10\.',   # Private Class A
            r'^172\.(1[6-9]|2[0-9]|3[0-1])\.',  # Private Class B
            r'^192\.168\.',  # Private Class C
            r'^169\.254\.',  # Link-local
            r'^0\.',    # Current network
            r'^::1$',   # IPv6 loopback
            r'^fc00:',  # IPv6 private
            r'^fe80:',  # IPv6 link-local
            r'^localhost$',
            r'^::ffff:127\.',  # IPv4-mapped IPv6 loopback
            r'^::ffff:10\.',   # IPv4-mapped IPv6 private
            r'^::ffff:192\.168\.',  # IPv4-mapped IPv6 private
        ]
        
        try:
            parsed = urlparse(url)
            
            # Only allow http and https schemes
            if parsed.scheme not in ('http', 'https'):
                raise ValueError(f"Invalid URL scheme '{parsed.scheme}'. Only http and https are allowed.")
            
            # Get the hostname
            hostname = parsed.hostname
            if not hostname:
                raise ValueError("Invalid URL: no hostname specified")
            
            # Resolve every address (IPv4 and IPv6); a hostname that returns any
            # private/loopback answer is rejected as a whole.
            try:
                infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC,
                                           socket.SOCK_STREAM)
            except socket.gaierror:
                raise ValueError(f"Could not resolve hostname '{hostname}'")
            resolved_ips = [info[4][0] for info in infos]
            if not resolved_ips:
                raise ValueError(f"Could not resolve hostname '{hostname}'")
            
            # Issue 7: Additional DNS rebinding protection
            # Check the hostname itself for suspicious patterns before relying on DNS resolution
            # DNS rebinding can exploit the time between hostname resolution and actual connection
            hostname_lower = hostname.lower()
            
            # Block common internal hostnames
            blocked_hostnames = ['localhost', 'localhost.localdomain', 'metadata', 'metadata.google.internal']
            if hostname_lower in blocked_hostnames:
                raise ValueError(f"Hostname '{hostname}' is not allowed")
            
            # Additional check: block hostnames that are pure IP addresses (could be used for rebinding)
            import re
            ip_pattern = re.compile(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$')
            if ip_pattern.match(hostname):
                raise ValueError(f"Direct IP address '{hostname}' is not allowed. Use a hostname instead.")
            
            # Block IPv6 addresses directly
            if ':' in hostname:  # Contains colons, likely IPv6
                raise ValueError(f"IPv6 address '{hostname}' is not allowed. Use a hostname instead.")
            
            # Check if resolved IP matches blocked patterns
            blocked_patterns_compiled = [re.compile(p) for p in blocked_ip_patterns]
            for pattern in blocked_patterns_compiled:
                if pattern.match(ip):
                    raise ValueError(f"URL resolves to blocked IP address '{ip}' - internal/network addresses are not allowed")
            
            # Make the HTTP request
            response = httpx.get(url, timeout=30.0)
            response.raise_for_status()
            
            # Validate content type before importing
            content_type = response.headers.get("content-type", "").lower()
            allowed_types = ["text/markdown", "text/plain", "text/x-markdown", "application/json"]
            
            # Also check for HTML which is not allowed
            if "html" in content_type:
                raise ValueError("Cannot import HTML content. Only markdown or plain text files are allowed.")
            
            # For JSON, check if it's a valid skill format
            if "json" in content_type:
                try:
                    data = response.json()
                    if "content" in data and "name" in data:
                        # It's a skill export JSON, convert to markdown
                        content = f"# {data['name']}\n\n{data.get('description', '')}\n\n{data['content']}"
                        file_name = f"{data.get('name', 'imported_skill')}.md"
                    else:
                        raise ValueError("Invalid skill JSON format")
                except json.JSONDecodeError:
                    raise ValueError("Invalid JSON content")
            else:
                content = response.text
                file_name = url.split("/")[-1] or "imported_skill.md"
            
            return self.import_from_file(content, file_name)
        except httpx.HTTPError as e:
            raise ValueError(f"Failed to fetch skill from URL: {str(e)}")
    
    def export_skill(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """Export a skill for download."""
        skill = self.backend.get_skill(skill_id)
        if not skill:
            return None
        
        # Create export package
        export_data = {
            "metadata": {
                "name": skill["name"],
                "description": skill["description"],
                "category": skill.get("category", "general"),
                "tags": skill.get("tags", []),
                "exported_at": skill.get("updated_at", skill.get("created_at"))
            },
            "content": skill["content"]
        }
        
        return {
            "skill": skill,
            "export_json": json.dumps(export_data, indent=2, ensure_ascii=False),
            "export_markdown": self._create_export_markdown(skill)
        }
    
    def get_categories(self) -> List[str]:
        """Get all unique categories."""
        skills = self.backend.list_skills()
        categories = set(s.get("category", "general") for s in skills)
        return sorted(list(categories))
    
    def get_all_tags(self) -> List[str]:
        """Get all unique tags across all skills."""
        skills = self.backend.list_skills()
        tags = set()
        for skill in skills:
            tags.update(skill.get("tags", []))
        return sorted(list(tags))
    
    def get_enabled_skills_content(self, skill_ids: List[str]) -> Dict[str, str]:
        """Get content for enabled skills (for system prompt injection)."""
        contents = {}
        for skill_id in skill_ids:
            content = self.backend.get_skill_content_only(skill_id)
            if content:
                contents[skill_id] = content
        return contents
    
    def _validate_skill_content(self, content: str) -> bool:
        """Validate that skill content is properly formatted."""
        # Basic validation - content should not be empty and should be markdown
        if not content or not content.strip():
            return False
        
        # Check for common markdown patterns
        # Skills should have some structure (headers, lists, etc.)
        has_structure = bool(re.search(r'^#{1,6}\s+', content, re.MULTILINE)) or \
                       bool(re.search(r'^[\-\*]\s+', content, re.MULTILINE)) or \
                       bool(re.search(r'^\d+\.\s+', content, re.MULTILINE))
        
        # Skills should be substantial (at least 100 characters)
        is_substantial = len(content.strip()) >= 100
        
        return has_structure and is_substantial
    
    def _extract_metadata_from_content(self, content: str, file_name: str) -> Dict[str, Any]:
        """Try to extract metadata from skill content, supporting YAML frontmatter."""
        metadata = {
            "name": self._extract_title(content) or self._clean_file_name(file_name),
            "description": self._extract_description(content),
            "category": "general",
            "tags": []
        }
        
        # Try to parse YAML frontmatter first (more robust)
        frontmatter_match = re.search(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
        if frontmatter_match:
            try:
                import yaml
                frontmatter = yaml.safe_load(frontmatter_match.group(1))
                if isinstance(frontmatter, dict):
                    if "name" in frontmatter:
                        metadata["name"] = frontmatter["name"]
                    if "description" in frontmatter:
                        metadata["description"] = frontmatter["description"]
                    if "category" in frontmatter:
                        metadata["category"] = frontmatter["category"]
                    if "tags" in frontmatter and isinstance(frontmatter["tags"], list):
                        metadata["tags"] = frontmatter["tags"]
            except ImportError as _exc:
                # yaml not available, fall back to regex
                logger.debug("ignored exception: %s", _exc)
            except Exception as _exc:
                # YAML parse error, fall back to regex
                logger.debug("ignored exception: %s", _exc)
        
        # Fall back to regex extraction for non-YAML formats
        if not frontmatter_match:
            # Try to extract category from content
            category_match = re.search(r'(?i)category:\s*(.+)', content)
            if category_match:
                metadata["category"] = category_match.group(1).strip()
            
            # Try to extract tags from content
            tags_match = re.search(r'(?i)tags:\s*(.+)', content)
            if tags_match:
                tags_str = tags_match.group(1)
                metadata["tags"] = [t.strip() for t in tags_str.split(",")]
        
        return metadata
    
    def _extract_title(self, content: str) -> Optional[str]:
        """Extract title from markdown content (first H1)."""
        match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        return match.group(1).strip() if match else None
    
    def _extract_description(self, content: str) -> str:
        """Extract description from content (first paragraph after title)."""
        # Remove title
        content_without_title = re.sub(r'^#\s+.+$', '', content, count=1, flags=re.MULTILINE)
        
        # Find first substantial paragraph
        paragraphs = content_without_title.strip().split('\n\n')
        for para in paragraphs:
            para = para.strip()
            if para and len(para) > 20 and not para.startswith('#'):
                return para[:200]  # Limit to 200 chars
        
        return "Imported skill"
    
    def _clean_file_name(self, file_name: str) -> str:
        """Clean file name to use as skill name."""
        # Remove extension
        name = re.sub(r'\.md$', '', file_name, flags=re.IGNORECASE)
        # Replace underscores and hyphens with spaces
        name = name.replace('_', ' ').replace('-', ' ')
        # Capitalize words
        return name.title()
    
    def _generate_import_id(self, name: str) -> str:
        """Generate a unique ID for imported skill."""
        import re
        from datetime import datetime
        
        # Clean name
        base_id = re.sub(r'[^a-zA-Z0-9\s]', '', name.lower())
        base_id = re.sub(r'\s+', '_', base_id)
        
        # Add timestamp to ensure uniqueness
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{base_id}_{timestamp}"
    
    def _create_export_markdown(self, skill: Dict[str, Any]) -> str:
        """Create a formatted markdown export of a skill with YAML frontmatter."""
        try:
            import yaml
            # Use YAML frontmatter for reliable metadata extraction on re-import
            frontmatter = yaml.dump({
                "name": skill['name'],
                "description": skill['description'],
                "category": skill.get('category', 'general'),
                "tags": skill.get('tags', [])
            }, default_flow_style=False, allow_unicode=True)
            
            return f"---\n{frontmatter}---\n\n{skill['content']}"
        except ImportError:
            # Fall back to plain text format if yaml not available
            lines = [
                f"# {skill['name']}",
                "",
                f"**Description:** {skill['description']}",
                "",
                f"**Category:** {skill.get('category', 'general')}",
                f"**Tags:** {', '.join(skill.get('tags', []))}",
                "---",
                "",
                skill['content']
            ]
            return "\n".join(lines)

