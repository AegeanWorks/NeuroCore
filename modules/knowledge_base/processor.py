import os
import re
import logging
import io
import hashlib
from typing import List, Dict, Tuple, Optional
import asyncio
import httpx
from core.llm import LLMBridge

class DocumentProcessor:
    """
    Document processor for PDF, DOCX, and TXT files.
    Adapted for NeuroCore to use LLMBridge and async processing.
    """

    # Maximum file size for TXT processing (100 MB)
    MAX_TXT_FILE_SIZE = 100 * 1024 * 1024

    def __init__(self, llm_bridge: LLMBridge, chunk_size: int = 1000, chunk_overlap: int = 100):
        self.llm = llm_bridge
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.log = logging.getLogger("NeuroCore.DocumentProcessor")

    async def process_document(self, file_path: str, progress_callback=None) -> Tuple[List[Dict], Optional[int], str]:
        """Auto-detect file type and process."""
        file_type = self.get_file_type(file_path)

        if file_type == 'pdf':
            chunks, page_count = await self.process_pdf(file_path, progress_callback)
            return chunks, page_count, 'pdf'
        elif file_type == 'docx':
            chunks, _ = await self.process_docx(file_path, progress_callback)
            return chunks, None, 'docx'
        elif file_type == 'txt':
            chunks, _ = await self.process_txt(file_path, progress_callback)
            return chunks, None, 'txt'
        else:
            raise ValueError(f"Unsupported file type: {file_path}")

    def get_file_type(self, filename: str) -> Optional[str]:
        ext = os.path.splitext(filename)[1].lower()
        if ext == '.pdf':
            return 'pdf'
        elif ext in ['.docx', '.doc']:
            return 'docx'
        elif ext in ['.txt', '.md', '.log', '.py', '.js', '.json']:
            return 'txt'
        return None

    async def process_pdf(self, file_path: str, progress_callback=None) -> Tuple[List[Dict], int]:
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise ImportError("PyMuPDF (fitz) not installed. Run: pip install PyMuPDF")

        # Check for OCR dependencies
        try:
            import pytesseract
            from PIL import Image
            OCR_AVAILABLE = True
        except ImportError:
            OCR_AVAILABLE = False

        try:
            with fitz.open(file_path) as doc:  # hygiene: allow pdf library document open
                page_count = len(doc)
                pages = []
                
                for page_num in range(page_count):
                    try:
                        page = doc[page_num]
                        text = page.get_text("text")

                        # OCR FALLBACK
                        if not text.strip() and OCR_AVAILABLE:
                            self.log.warning(f"Page {page_num + 1} seems empty. Attempting OCR...")
                            try:
                                pix = page.get_pixmap()
                                img_data = pix.tobytes("png")
                                with Image.open(io.BytesIO(img_data)) as image:  # hygiene: allow pil image open
                                    text = pytesseract.image_to_string(image)
                            except Exception as e:
                                self.log.error(f"OCR failed for page {page_num + 1}: {e}")

                        pages.append({
                            'page_number': page_num + 1,
                            'text': self.clean_text(text)
                        })
                    except Exception as e:
                        self.log.warning(f"Skipping page {page_num + 1} due to error: {e}")
                        continue
        except Exception as e:
            raise ValueError(f"Could not open PDF: {e}")
        
        if not pages:
            raise ValueError("No text extracted from PDF.")

        chunks = self._chunk_pages(pages)
        await self._generate_embeddings(chunks, progress_callback)
        return chunks, page_count

    async def process_docx(self, file_path: str, progress_callback=None) -> Tuple[List[Dict], None]:
        try:
            from docx import Document
        except ImportError:
            raise ImportError("python-docx not installed. Run: pip install python-docx")

        doc = Document(file_path)
        full_text = "\n\n".join([para.text for para in doc.paragraphs if para.text.strip()])
        chunks = self._chunk_text(self.clean_text(full_text))
        await self._generate_embeddings(chunks, progress_callback)
        return chunks, None

    async def process_txt(self, file_path: str, progress_callback=None) -> Tuple[List[Dict], None]:
        # Check file size before opening to prevent memory exhaustion
        file_size = os.path.getsize(file_path)
        if file_size > self.MAX_TXT_FILE_SIZE:
            raise ValueError(
                f"File too large: {file_size / (1024*1024):.1f} MB. "
                f"Maximum allowed size is {self.MAX_TXT_FILE_SIZE / (1024*1024):.0f} MB."
            )
        
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read()
        chunks = self._chunk_text(self.clean_text(text))
        await self._generate_embeddings(chunks, progress_callback)
        return chunks, None

    async def _generate_embeddings(self, chunks: List[Dict], progress_callback=None):
        """Generate embeddings using the LLMBridge."""
        # Use a semaphore to limit concurrency
        sem = asyncio.Semaphore(5)
        
        # Use a single client for all requests to reuse connections (Speed)
        # Note: We don't use async with here because we need the client to remain
        # open until all gather tasks complete, including edge cases with exceptions
        client = httpx.AsyncClient(timeout=60.0)
        try:
            # Create a temp bridge that uses this shared client
            batch_bridge = LLMBridge(
                base_url=self.llm.base_url,
                api_key=self.llm.api_key,
                embedding_base_url=self.llm.embedding_base_url,
                embedding_model=self.llm.embedding_model,
                client=client
            )

            total = len(chunks)
            completed = 0

            async def process_chunk(chunk):
                async with sem:
                    emb = await batch_bridge.get_embedding(chunk['text'])
                    if emb:
                        chunk['embedding'] = emb
                    else:
                        # Embedding call returned empty/None - mark as failed
                        chunk['embedding'] = None
                    nonlocal completed
                    completed += 1
                    if progress_callback:
                        await progress_callback(completed, total)

            # Use return_exceptions=True to handle individual failures without breaking the whole batch
            results = await asyncio.gather(*(process_chunk(chunk) for chunk in chunks), return_exceptions=True)
            
            # Check for exceptions and log them
            for chunk, result in zip(chunks, results):
                if isinstance(result, Exception):
                    self.log.warning(f'Embedding failed for chunk: {result}')
                    chunk['embedding'] = None  # Mark as failed, skip FAISS indexing
                    
        finally:
            # Always close the client after all tasks complete
            await client.aclose()

    def _chunk_pages(self, pages: List[Dict]) -> List[Dict]:
        chunks = []
        full_text = ""
        page_map = [] 

        current_pos = 0
        for page in pages:
            text = page['text'] + "\n"
            full_text += text
            end_pos = current_pos + len(text)
            page_map.append((current_pos, end_pos, page['page_number']))
            current_pos = end_pos

        pos = 0
        while pos < len(full_text):
            candidate = full_text[pos : pos + self.chunk_size]
            if pos + self.chunk_size < len(full_text):
                candidate = self._break_at_sentence(candidate)

            page_number = 1
            for start, end, p_num in page_map:
                if start <= pos < end:
                    page_number = p_num
                    break

            if candidate.strip():
                text = candidate.strip()
                chunks.append({
                    'text': text,
                    'page_number': page_number,
                    'chunk_hash': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                })

            step = max(1, len(candidate) - self.chunk_overlap)
            pos += step

        return chunks

    def _chunk_text(self, text: str) -> List[Dict]:
        chunks = []
        current_pos = 0
        while current_pos < len(text):
            chunk_text = text[current_pos:current_pos + self.chunk_size]
            if current_pos + self.chunk_size < len(text):
                chunk_text = self._break_at_sentence(chunk_text)

            if chunk_text.strip():
                text = chunk_text.strip()
                chunks.append({
                    'text': text,
                    'chunk_hash': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                })

            current_pos += max(1, len(chunk_text) - self.chunk_overlap)
        return chunks

    def _break_at_sentence(self, text: str) -> str:
        matches = list(re.finditer(r'(?<=[.!?])\s+(?=[A-Z])|\n', text))
        if matches:
            last_match = matches[-1]
            return text[:last_match.end()]
        last_space = text.rfind(' ')
        if last_space > 0:
            return text[:last_space]
        return text

    def clean_text(self, text: str) -> str:
        text = re.sub(r'(\w+)-\n(\w+)', r'\1\2', text)
        text = re.sub(r'(?<!\n)\n(?!\n)(?!\s*([-*•]|\d+\.))', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' {2,}', ' ', text)
        text = text.strip()
        text = re.sub(r'(?i)all rights reserved\.?', '', text)
        text = re.sub(r'(?i)copyright © \d{4}', '', text)
        return text