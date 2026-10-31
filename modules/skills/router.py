"""
Router for Skills module - API endpoints for skill management.
"""

import json
from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from typing import List, Optional

from .service import SkillService

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/gui", response_class=HTMLResponse)
async def skills_gui(request: Request):
    """Returns the skills management GUI."""
    return templates.TemplateResponse(request, "skills_gui.html", {})


@router.get("/api/skills")
async def list_skills(category: Optional[str] = None, tag: Optional[str] = None):
    """List all skills with optional filtering."""
    service = SkillService()
    skills = service.list_skills(category=category, tag=tag)
    return {"skills": skills}


@router.get("/api/skills/categories")
async def get_categories():
    """Get all unique skill categories."""
    service = SkillService()
    categories = service.get_categories()
    return {"categories": categories}


@router.get("/api/skills/tags")
async def get_tags():
    """Get all unique skill tags."""
    service = SkillService()
    tags = service.get_all_tags()
    return {"tags": tags}


@router.get("/api/skills/{skill_id}")
async def get_skill(skill_id: str):
    """Get a specific skill by ID."""
    service = SkillService()
    skill = service.get_skill(skill_id)
    
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    
    return skill


@router.post("/api/skills")
async def create_skill(request: Request):
    """Create a new skill."""
    data = await request.json()
    
    required_fields = ["name", "description", "content"]
    for field in required_fields:
        if field not in data or not data[field]:
            raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
    
    service = SkillService()
    try:
        skill = service.create_skill(
            name=data["name"],
            description=data["description"],
            content=data["content"],
            category=data.get("category", "general"),
            tags=data.get("tags", [])
        )
        return skill
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/api/skills/{skill_id}")
async def update_skill(skill_id: str, request: Request):
    """Update an existing skill."""
    data = await request.json()
    service = SkillService()
    
    # Check if skill exists
    existing = service.get_skill(skill_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Skill not found")
    
    try:
        updated = service.update_skill(
            skill_id=skill_id,
            name=data.get("name"),
            description=data.get("description"),
            content=data.get("content"),
            category=data.get("category"),
            tags=data.get("tags")
        )
        return updated
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/skills/{skill_id}")
async def delete_skill(skill_id: str):
    """Delete a skill."""
    service = SkillService()
    
    if not service.delete_skill(skill_id):
        raise HTTPException(status_code=404, detail="Skill not found")
    
    return {"message": "Skill deleted successfully"}


@router.post("/api/skills/import/file")
async def import_from_file(file: UploadFile = File(...)):
    """Import a skill from an uploaded file."""
    if not file.filename.endswith('.md'):
        raise HTTPException(status_code=400, detail="Only .md files are supported")
    
    content = await file.read()
    try:
        content_str = content.decode('utf-8')
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be valid UTF-8 text")
    
    service = SkillService()
    try:
        skill = service.import_from_file(content_str, file.filename)
        return skill
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/skills/import/url")
async def import_from_url(request: Request):
    """Import a skill from a URL."""
    data = await request.json()
    url = data.get("url")
    
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    
    service = SkillService()
    try:
        skill = service.import_from_url(url)
        return skill
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/skills/{skill_id}/export")
async def export_skill(skill_id: str, format: str = "json"):
    """Export a skill in various formats."""
    service = SkillService()
    export_data = service.export_skill(skill_id)
    
    if not export_data:
        raise HTTPException(status_code=404, detail="Skill not found")
    
    skill = export_data["skill"]
    
    if format == "json":
        # Parse the JSON string to return a proper JSON object (not double-encoded)
        return JSONResponse(
            content=json.loads(export_data["export_json"]),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{skill_id}.json"'}
        )
    elif format == "markdown":
        return PlainTextResponse(
            content=export_data["export_markdown"],
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{skill_id}.md"'}
        )
    else:
        raise HTTPException(status_code=400, detail="Invalid format. Use 'json' or 'markdown'")


@router.get("/api/skills/{skill_id}/content")
async def get_skill_content(skill_id: str):
    """Get only the content of a skill (for system prompt injection)."""
    service = SkillService()
    content = service.backend.get_skill_content_only(skill_id)
    
    if content is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    
    return {"content": content}


@router.post("/api/skills/bulk/enable")
async def get_enabled_skills_content(request: Request):
    """Get content for multiple skills (for system prompt injection)."""
    data = await request.json()
    skill_ids = data.get("skill_ids", [])
    
    if not skill_ids:
        return {"contents": {}}
    
    service = SkillService()
    contents = service.get_enabled_skills_content(skill_ids)
    
    return {"contents": contents}


@router.get("/api/skills/{skill_id}/versions")
async def list_skill_versions(skill_id: str):
    """Version history for one skill, newest last, with the active version."""
    service = SkillService()
    if not service.backend.skill_exists(skill_id):
        raise HTTPException(status_code=404, detail="Skill not found")
    return {
        "skill_id": skill_id,
        "active": service.backend.versions.active(skill_id),
        "versions": service.backend.list_versions(skill_id),
    }


@router.get("/api/skills/{skill_id}/versions/{version}")
async def get_skill_version(skill_id: str, version: int):
    """One skill version, including its exact content."""
    service = SkillService()
    record = service.backend.get_version(skill_id, version)
    if record is None:
        raise HTTPException(status_code=404, detail="Skill version not found")
    return record


@router.post("/api/skills/{skill_id}/rollback/{version}")
async def rollback_skill(skill_id: str, version: int):
    """Restore a version; the restore is recorded as a new version, never a rewrite."""
    service = SkillService()
    if not service.backend.skill_exists(skill_id):
        raise HTTPException(status_code=404, detail="Skill not found")
    record = service.backend.get_version(skill_id, version)
    if record is None:
        raise HTTPException(status_code=404, detail="Skill version not found")
    restored = service.backend.rollback(skill_id, version)
    return {"status": "rolled_back", "restored_from": version, "skill": restored}
