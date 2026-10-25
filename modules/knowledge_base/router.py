import json
import logging
import os
import shutil
import threading
import time as import_time
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

import numpy as np
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from core.dependencies import get_llm_bridge
from core.llm import LLMBridge
from core.paths import project_path
from core.settings import settings

from .backend import document_store
from .processor import DocumentProcessor

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

UPLOAD_DIR = project_path("data", "uploaded_docs")
PROCESSED_DIR = project_path("data", "processed_docs")


def _format_size(size_bytes: int) -> str:
    """Return a human-readable file size string."""
    if size_bytes is None:
        return ""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"

if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)
if not os.path.exists(PROCESSED_DIR):
    os.makedirs(PROCESSED_DIR)


def _resolve_stored_path(document: dict, upload_dir: str = None):
    """Return the on-disk path for a document, including legacy UUID-prefixed uploads."""
    directory = upload_dir or UPLOAD_DIR
    stored = document.get("stored_filename")
    filename = stored or document.get("filename")
    if not filename:
        return None

    path = os.path.join(directory, os.path.basename(filename))
    if os.path.exists(path) or stored:
        return path

    legacy_suffix = "_" + os.path.basename(filename)
    try:
        matches = [
            name for name in os.listdir(directory)
            if name.endswith(legacy_suffix)
        ]
    except OSError:
        matches = []
    if len(matches) == 1:
        return os.path.join(directory, matches[0])
    return path


def _document_storage_names(documents: list, disk_files: set) -> set:
    """Map document rows to the filenames that should exist in the upload directory."""
    names = set()
    for document in documents:
        stored = document.get("stored_filename")
        if stored:
            names.add(stored)
            continue
        legacy = document.get("filename")
        if not legacy:
            continue
        matches = [name for name in disk_files if name.endswith("_" + legacy)]
        names.add(matches[0] if len(matches) == 1 else legacy)
    return names

# --- Security Configuration ---
ALLOWED_EXTENSIONS = {'.pdf', '.md', '.txt', '.docx', '.html', '.json'}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

def validate_upload(file: UploadFile) -> None:
    """
    Validate file upload for security.
    
    Checks:
    - File extension is in allowed list
    - File size is within limits
    - Filename doesn't contain path traversal attempts
    
    Raises:
        HTTPException: If validation fails
    """
    # Check filename for path traversal
    filename = file.filename or "unknown"
    if '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(400, "Invalid filename: path traversal detected")
    
    # Check file extension
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400, 
            f"Invalid file type '{ext}'. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}"
        )
    
    # Check content type (basic validation)
    allowed_content_types = {
        'application/pdf', 'text/plain', 'text/markdown', 
        'text/html', 'application/json', 
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/msword'
    }
    if file.content_type and file.content_type not in allowed_content_types:
        # Allow empty/unknown content type but warn for clearly wrong types
        if file.content_type and file.content_type.startswith(('image/', 'video/', 'audio/', 'executable/')):
            raise HTTPException(400, f"Invalid content type: {file.content_type}")

# --- Progress Tracking ---
upload_progress = {}
UPLOAD_PROGRESS_TTL = 3600  # 1 hour TTL for stale entries
upload_progress_lock = threading.Lock()  # Thread safety for concurrent access

def _cleanup_stale_uploads():
    """Remove stale entries from upload_progress that exceed TTL."""
    import time
    current_time = time.time()
    stale_keys = []
    with upload_progress_lock:
        for tracking_id, info in upload_progress.items():
            # Check if entry is older than TTL (only for pending/processing entries)
            if info.get("status") in ("pending", "processing"):
                # Check created_at timestamp if available
                created_at = info.get("created_at", current_time)
                if current_time - created_at > UPLOAD_PROGRESS_TTL:
                    stale_keys.append(tracking_id)
        for key in stale_keys:
            del upload_progress[key]
    return len(stale_keys)

async def process_and_save_document(tracking_id: str, file_path: str, original_filename: str, stored_filename: str, llm: LLMBridge):
    """Background task to process document and update progress."""
    try:
        with upload_progress_lock:
            upload_progress[tracking_id]["status"] = "processing"
        
        async def progress_callback(current, total):
            with upload_progress_lock:
                if upload_progress.get(tracking_id, {}).get("status") == "aborted":
                    raise Exception("ABORTED")

                if total > 0:
                    percent = int((current / total) * 100)
                    upload_progress[tracking_id]["progress"] = percent

        processor = DocumentProcessor(llm)
        chunks, page_count, file_type = await processor.process_document(file_path, progress_callback=progress_callback)
        
        with upload_progress_lock:
            if upload_progress.get(tracking_id, {}).get("status") == "aborted":
                raise Exception("ABORTED")

        # Save chunks to disk
        # (We need to re-fetch doc ID logic or just use hash/filename, but here we use a temp ID logic or just skip file saving if not strictly needed for DB)
        # For simplicity, we'll use the file hash as the ID for the json file later or just skip the JSON file if DB is enough.
        # But existing code used new_doc['id'] which wasn't defined in the previous snippet properly before DB insert.
        # Let's stick to DB insertion.

        # Convert embeddings to numpy for FAISS
        for c in chunks:
            if isinstance(c['embedding'], list):
                c['embedding'] = np.array(c['embedding'], dtype='float32')

        file_hash = document_store.compute_file_hash(file_path)
        file_size = os.path.getsize(file_path)

        # Check if a document with this filename already exists
        existing_doc = document_store.get_document_by_filename(original_filename)

        if existing_doc:
            if existing_doc['file_hash'] == file_hash:
                # Byte-for-byte identical — nothing to do
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except OSError as e:
                        logger.warning(f"Failed to remove duplicate file {stored_filename}: {e}")
                with upload_progress_lock:
                    upload_progress[tracking_id]["status"] = "done"
                    upload_progress[tracking_id]["progress"] = 100
                    upload_progress[tracking_id]["message"] = "Document already exists (no changes)"
                return

            # Same filename, different content — perform delta re-index and keep the new
            # upload, replacing the previous stored file.
            old_path = _resolve_stored_path(existing_doc)
            result = document_store.reindex_document(
                existing_doc['id'], file_hash, file_size, page_count, chunks,
                stored_filename=stored_filename,
            )
            if (old_path and os.path.exists(old_path)
                    and os.path.basename(old_path) != stored_filename):
                try:
                    os.remove(old_path)
                except OSError as e:
                    logger.warning(f"Failed to remove replaced file {old_path}: {e}")
            with upload_progress_lock:
                upload_progress[tracking_id]["status"] = "done"
                upload_progress[tracking_id]["progress"] = 100
                upload_progress[tracking_id]["message"] = (
                    f"Re-indexed: +{result['added']} new, -{result['removed']} removed, "
                    f"{result['unchanged']} unchanged chunks"
                )
            return

        # No document with this filename — check for cross-filename hash duplicate
        if document_store.document_exists(file_hash):
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.info(f"Removed duplicate file: {stored_filename}")
                except OSError as e:
                    logger.warning(f"Failed to remove duplicate file {stored_filename}: {e}")
            with upload_progress_lock:
                upload_progress[tracking_id]["status"] = "done"
                upload_progress[tracking_id]["progress"] = 100
                upload_progress[tracking_id]["message"] = "Document already exists (duplicate content)"
            return

        # Truly new document — add it and remember the on-disk name
        document_store.add_document(
            file_hash, original_filename, file_type, file_size, page_count, chunks,
            stored_filename=stored_filename,
        )

        with upload_progress_lock:
            upload_progress[tracking_id]["status"] = "done"
            upload_progress[tracking_id]["progress"] = 100

    except Exception as e:
        with upload_progress_lock:
            if str(e) == "ABORTED" or upload_progress.get(tracking_id, {}).get("status") == "aborted":
                upload_progress[tracking_id]["status"] = "aborted"
            else:
                logger.error(f"Error processing document {original_filename}: {e}")
                upload_progress[tracking_id]["status"] = "error"
                upload_progress[tracking_id]["message"] = str(e)
            
        # Cleanup file
        if os.path.exists(file_path):
            try: 
                os.remove(file_path)
            except OSError as _exc:
                logger.debug("ignored exception: %s", _exc)

@router.get("", response_class=HTMLResponse)
async def knowledge_base_page(request: Request):
    module_manager = request.app.state.module_manager
    enabled_modules = [m for m in module_manager.get_all_modules() if m.get("enabled")]
    
    return templates.TemplateResponse(request, "index.html", {
        "modules": enabled_modules,
        "active_module": "knowledge_base",
        "settings": settings.settings,
        "full_width_content": True
    })

@router.get("/gui", response_class=HTMLResponse)
async def get_gui(request: Request):
    return templates.TemplateResponse(request, "knowledge_base_gui.html", {})

@router.get("/list", response_class=HTMLResponse)
async def list_docs(request: Request, q: str = Query(None)):
    documents = document_store.list_documents()
    
    # Format for template
    for doc in documents:
        doc["date"] = datetime.fromtimestamp(doc["created_at"]).strftime("%Y-%m-%d %H:%M")
        # Map backend keys to template keys
        doc["type"] = doc.pop("file_type")
        doc["pages"] = doc.pop("page_count")
        doc["chunks"] = doc.pop("chunk_count")
        doc["size"] = _format_size(doc.pop("file_size", None))

    if q:
        documents = [d for d in documents if q.lower() in d.get('filename', '').lower()]
        
    return templates.TemplateResponse(request, "knowledge_base_list.html", {"documents": documents})

@router.post("/upload", response_class=HTMLResponse)
async def upload_doc(request: Request, background_tasks: BackgroundTasks, files: List[UploadFile] = File(...), llm: LLMBridge = Depends(get_llm_bridge)):
    # Clean up stale entries before processing new uploads
    try:
        _cleanup_stale_uploads()
    except Exception as _exc:
        logger.debug("ignored exception: %s", _exc)  # Non-critical, continue with upload
    
    items_html = []
    errors = []

    for file in files:
        # Validate file before processing
        try:
            validate_upload(file)
        except HTTPException as e:
            errors.append(f"{file.filename}: {e.detail}")
            continue
        
        # Check file size (read first chunk to check size without loading entire file)
        file.file.seek(0, 2)  # Seek to end
        file_size = file.file.tell()
        file.file.seek(0)  # Reset to beginning
        
        if file_size > MAX_FILE_SIZE:
            errors.append(f"{file.filename}: File too large ({file_size / (1024*1024):.1f}MB > {MAX_FILE_SIZE / (1024*1024):.0f}MB limit)")
            continue
        
        tracking_id = str(uuid.uuid4())
        
        # Use UUID-prefixed filename to prevent collisions and handle reserved names
        safe_filename = f"{uuid.uuid4()}_{file.filename}"
        file_path = os.path.join(UPLOAD_DIR, safe_filename)
        
        # Save file to disk immediately
        try:
            with open(file_path, "wb+") as buffer:
                shutil.copyfileobj(file.file, buffer)
        except Exception as e:
            errors.append(f"Error saving {file.filename}: {e}")
            continue

        # Initialize progress with created_at timestamp for TTL tracking
        with upload_progress_lock:
            upload_progress[tracking_id] = {
                "filename": file.filename,
                "stored_filename": safe_filename,
                "progress": 0,
                "status": "pending",
                "created_at": import_time.time()
            }
        
        # Start background processing - pass both original filename and stored filename
        background_tasks.add_task(process_and_save_document, tracking_id, file_path, file.filename, safe_filename, llm)

        # Return progress item
        tmpl = templates.env.get_template("knowledge_base_progress_item.html")
        items_html.append(tmpl.render(tracking_id=tracking_id, filename=file.filename, progress=0))

    if len(items_html) > 3:
        visible_items = "".join(items_html[:3])
        hidden_items = "".join(items_html[3:])
        remaining_count = len(items_html) - 3
        batch_id = str(uuid.uuid4())
        
        response_html = f"""
        <div class="batch-container mb-2">
            {visible_items}
            <div id="batch-{batch_id}" class="hidden">
                {hidden_items}
            </div>
            <button id="expand-{batch_id}" 
                    onclick="document.getElementById('batch-{batch_id}').classList.remove('hidden'); this.classList.add('hidden'); document.getElementById('collapse-{batch_id}').classList.remove('hidden')" 
                    class="text-xs text-slate-500 hover:text-slate-300 w-full text-left py-1 pl-1 italic transition-colors">
                ... ({remaining_count} more processing)
            </button>
            <button id="collapse-{batch_id}" 
                    onclick="document.getElementById('batch-{batch_id}').classList.add('hidden'); this.classList.add('hidden'); document.getElementById('expand-{batch_id}').classList.remove('hidden')" 
                    class="hidden text-xs text-slate-500 hover:text-slate-300 w-full text-left py-1 pl-1 italic transition-colors">
                ^ Show less
            </button>
        </div>
        """
    else:
        response_html = "".join(items_html)

    headers = {}
    if errors:
        headers["HX-Trigger"] = json.dumps({"showMessage": {"level": "error", "message": " | ".join(errors)}})

    return HTMLResponse(content=response_html, headers=headers)

@router.get("/upload/progress/{tracking_id}", response_class=HTMLResponse)
async def get_upload_progress(request: Request, tracking_id: str):
    # Clean up stale entries periodically during progress polling
    try:
        _cleanup_stale_uploads()
    except Exception as _exc:
        logger.debug("ignored exception: %s", _exc)  # Non-critical
    
    with upload_progress_lock:
        info = upload_progress.get(tracking_id)
        if not info:
            return Response(status_code=404)
        
        if info["status"] == "done":
            del upload_progress[tracking_id]
            return Response(content="", headers={"HX-Trigger": json.dumps({"docsChanged": None, "showMessage": {"level": "success", "message": f"Processed {info['filename']}"}})})
        
        if info["status"] == "aborted":
            del upload_progress[tracking_id]
            return Response(content="", headers={"HX-Trigger": json.dumps({"showMessage": {"level": "info", "message": "Upload aborted."}})})

        if info["status"] == "error":
            msg = info.get("message", "Unknown error")
            del upload_progress[tracking_id]
            return Response(content="", headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Failed: {msg}"}})})

        return templates.TemplateResponse(request, "knowledge_base_progress_item.html", {"tracking_id": tracking_id, "filename": info["filename"], "progress": info["progress"]})

@router.post("/upload/abort/{tracking_id}")
async def abort_upload(request: Request, tracking_id: str):
    with upload_progress_lock:
        if tracking_id in upload_progress:
            upload_progress[tracking_id]["status"] = "aborted"
    return Response(status_code=200)

@router.delete("/delete/{doc_id}", response_class=HTMLResponse)
async def delete_doc(request: Request, doc_id: int):
    # Get filename before deleting to remove file from disk
    doc_to_delete = document_store.get_document(doc_id)
    
    if doc_to_delete:
        file_path = _resolve_stored_path(doc_to_delete)
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError as e:
                logger.warning(f"Error deleting file {file_path}: {e}")

    # Remove processed chunks file
    chunks_path = os.path.join(PROCESSED_DIR, f"{doc_id}.json")
    if os.path.exists(chunks_path):
        try: 
            os.remove(chunks_path)
        except OSError as _exc:
            logger.debug("ignored exception: %s", _exc)
    
    document_store.delete_document(doc_id)
    return await list_docs(request)

@router.delete("/delete_all", response_class=HTMLResponse)
async def delete_all_docs(request: Request):
    document_store.clear()
    
    # Also clean up files
    for f in os.listdir(UPLOAD_DIR):
        try:
            os.remove(os.path.join(UPLOAD_DIR, f))
        except OSError as _exc:
            logger.debug("ignored exception: %s", _exc)
    
    for f in os.listdir(PROCESSED_DIR):
        try:
            os.remove(os.path.join(PROCESSED_DIR, f))
        except OSError as _exc:
            logger.debug("ignored exception: %s", _exc)
        
    return await list_docs(request)

@router.post("/reindex/{doc_id}")
async def reindex_doc(doc_id: int, llm: LLMBridge = Depends(get_llm_bridge)):
    """Re-embed all chunks for a document using the current embedding model.

    The original file does not need to be on disk — chunk text is read from SQLite.
    """
    doc = document_store.get_document(doc_id)
    if not doc:
        return Response(
            status_code=200,
            headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Document not found"}})},
        )

    chunks = document_store.get_document_chunks(doc_id)
    if not chunks:
        return Response(
            status_code=200,
            headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"No chunks found for '{doc['filename']}'"}})},
        )

    chunk_embeddings = []
    for chunk in chunks:
        try:
            emb = await llm.get_embedding(chunk["text"])
            chunk_embeddings.append((chunk["id"], np.array(emb, dtype="float32") if emb else None))
        except Exception as e:
            logger.warning(f"Failed to embed chunk {chunk['id']} of doc {doc_id}: {e}")
            chunk_embeddings.append((chunk["id"], None))

    result = document_store.reembed_document(doc_id, chunk_embeddings)

    msg = f"Re-indexed '{doc['filename']}': {result['updated']} chunks updated"
    if result["failed"]:
        msg += f", {result['failed']} failed (no embedding model?)"
    level = "success" if result["failed"] == 0 else "warning"

    return Response(
        status_code=200,
        headers={"HX-Trigger": json.dumps({"docsChanged": None, "showMessage": {"level": level, "message": msg}})},
    )


@router.post("/integrity_check", response_class=HTMLResponse)
async def check_integrity(request: Request):
    broken_docs = document_store.find_broken_documents()
    orphans = document_store.get_orphaned_chunk_count()
    
    # Check for ghost files
    raw_docs = document_store.list_documents()
    disk_files = set(os.listdir(UPLOAD_DIR))
    db_filenames = _document_storage_names(raw_docs, disk_files)

    ghost_files = [f for f in disk_files if f not in db_filenames]
    missing_files = [f for f in db_filenames if f not in disk_files]
    
    message = "Integrity Check Passed: System is in sync."
    level = "success"
    
    if ghost_files or missing_files or broken_docs or orphans > 0:
        level = "warning"
        message = f"Issues: {len(ghost_files)} ghosts, {len(missing_files)} missing, {len(broken_docs)} broken, {orphans} orphans."

    # Return empty content but trigger a toast notification
    return Response(content="", headers={"HX-Trigger": json.dumps({"showMessage": {"level": level, "message": message}})})
