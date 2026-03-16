from fastapi import APIRouter, HTTPException, UploadFile, File, Form
import aiosqlite
import uuid
import os

from services.ai_engine import vector_store
from utils.logger import logger

router = APIRouter()
DB_PATH = os.getenv("DB_PATH", "./neuralinbox.db")


def chunk_text(text, chunk_size=800):
    words = text.split()
    chunks = []
    overlap = 50
    for i in range(0, len(words), chunk_size - overlap):
        chunk = " ".join(words[i:i + chunk_size])
        chunks.append(chunk)
    return chunks


@router.get("")
async def list_documents():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, name, file_type, department, created_at, chunk_index FROM knowledge_docs ORDER BY created_at DESC"
        ) as cursor:
            docs = [dict(row) for row in await cursor.fetchall()]
    grouped = {}
    for doc in docs:
        name = doc["name"]
        if name not in grouped:
            grouped[name] = {"id": doc["id"], "name": name, "file_type": doc["file_type"],
                             "department": doc["department"], "created_at": doc["created_at"], "chunks": 0}
        grouped[name]["chunks"] += 1
    return {"documents": list(grouped.values())}


@router.post("/upload")
async def upload_document(file: UploadFile = File(...), department: str = Form("general")):
    content_bytes = await file.read()
    try:
        if file.filename.endswith(".pdf"):
            try:
                import PyPDF2
                import io
                reader = PyPDF2.PdfReader(io.BytesIO(content_bytes))
                text = " ".join([page.extract_text() or "" for page in reader.pages])
            except ImportError:
                text = content_bytes.decode("utf-8", errors="replace")
        else:
            text = content_bytes.decode("utf-8", errors="replace")
    except Exception:
        text = content_bytes.decode("utf-8", errors="replace")

    if not text.strip():
        raise HTTPException(status_code=400, detail="Could not extract text from document")

    chunks = chunk_text(text)
    async with aiosqlite.connect(DB_PATH) as db:
        for i, chunk in enumerate(chunks):
            doc_id = str(uuid.uuid4())
            await db.execute("""
                INSERT INTO knowledge_docs (id, name, file_type, content, chunk_index, department)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (doc_id, file.filename, file.content_type, chunk, i, department))
            vector_store.add_document(doc_id, chunk, {"name": file.filename, "department": department})
        await db.commit()

    logger.info(f"Uploaded {file.filename}: {len(chunks)} chunks")
    return {"success": True, "filename": file.filename, "chunks": len(chunks)}


@router.post("/text")
async def add_text_knowledge(payload: dict):
    name = payload.get("name", "Manual Entry")
    content = payload.get("content", "")
    department = payload.get("department", "general")
    if not content:
        raise HTTPException(status_code=400, detail="Content is required")
    chunks = chunk_text(content)
    async with aiosqlite.connect(DB_PATH) as db:
        for i, chunk in enumerate(chunks):
            doc_id = str(uuid.uuid4())
            await db.execute("""
                INSERT INTO knowledge_docs (id, name, file_type, content, chunk_index, department)
                VALUES (?, ?, 'text', ?, ?, ?)
            """, (doc_id, name, chunk, i, department))
            vector_store.add_document(doc_id, chunk, {"name": name, "department": department})
        await db.commit()
    return {"success": True, "name": name, "chunks": len(chunks)}


@router.delete("/{doc_name}")
async def delete_document(doc_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM knowledge_docs WHERE name = ?", (doc_name,))
        await db.commit()
    return {"success": True}
