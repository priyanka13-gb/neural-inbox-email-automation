from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import aiosqlite
import uuid
from datetime import datetime
import os

from services.ai_engine import AIEngine
from services.smtp_sender import SMTPSender
from utils.logger import logger

router = APIRouter()
DB_PATH = os.getenv("DB_PATH", "./neuralinbox.db")
ai_engine = AIEngine()
smtp_sender = SMTPSender()


class ApproveRequest(BaseModel):
    reply_body: Optional[str] = None

class RegenerateRequest(BaseModel):
    custom_instructions: Optional[str] = None

class ManualEmailRequest(BaseModel):
    sender_email: str
    sender_name: str
    subject: str
    body: str


@router.get("")
async def list_emails(
    status: Optional[str] = None,
    department: Optional[str] = None,
    urgency: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 20
):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        where_clauses = []
        params = []
        if status:
            where_clauses.append("status = ?")
            params.append(status)
        if department:
            where_clauses.append("department = ?")
            params.append(department)
        if urgency:
            where_clauses.append("urgency = ?")
            params.append(urgency)
        if search:
            where_clauses.append("(subject LIKE ? OR sender_email LIKE ? OR sender_name LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        offset = (page - 1) * limit
        async with db.execute(f"SELECT * FROM emails {where_sql} ORDER BY received_at DESC LIMIT ? OFFSET ?",
                               params + [limit, offset]) as cursor:
            emails = [dict(row) for row in await cursor.fetchall()]
        async with db.execute(f"SELECT COUNT(*) FROM emails {where_sql}", params) as cursor:
            total = (await cursor.fetchone())[0]
    return {"emails": emails, "total": total, "page": page, "pages": (total + limit - 1) // limit}


@router.get("/{email_id}")
async def get_email(email_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM emails WHERE id = ?", (email_id,)) as cursor:
            row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Email not found")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM activity_log WHERE email_id = ? ORDER BY created_at", (email_id,)) as cursor:
            activity = [dict(r) for r in await cursor.fetchall()]
    result = dict(row)
    result["activity"] = activity
    return result


@router.post("/{email_id}/approve")
async def approve_email(email_id: str, request: ApproveRequest):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM emails WHERE id = ?", (email_id,)) as cursor:
            email_row = await cursor.fetchone()
    if not email_row:
        raise HTTPException(status_code=404, detail="Email not found")
    email_row = dict(email_row)
    reply_body = request.reply_body or email_row["ai_draft"]
    human_edited = 1 if request.reply_body and request.reply_body != email_row["ai_draft"] else 0
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM email_settings WHERE id = 1") as cursor:
            settings = dict(await cursor.fetchone())
    try:
        await smtp_sender.send_reply(
            to_email=email_row["sender_email"],
            to_name=email_row["sender_name"],
            subject=email_row["subject"],
            reply_body=reply_body,
            original_message_id=email_row["message_id"],
            settings=settings
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send: {str(e)}")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE emails SET status = 'sent', final_reply = ?, replied_at = ?,
            human_edited = ?, auto_sent = 0 WHERE id = ?
        """, (reply_body, datetime.utcnow().isoformat(), human_edited, email_id))
        await db.execute(
            "INSERT INTO activity_log (id, email_id, action, actor, details) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), email_id, "approved_sent", "human",
             "Edited and sent" if human_edited else "Approved AI draft")
        )
        await db.commit()
    return {"success": True, "message": "Reply sent successfully"}


@router.post("/{email_id}/reject")
async def reject_email(email_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE emails SET status = 'rejected' WHERE id = ?", (email_id,))
        await db.execute(
            "INSERT INTO activity_log (id, email_id, action, actor) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), email_id, "rejected", "human")
        )
        await db.commit()
    return {"success": True}


@router.post("/{email_id}/regenerate")
async def regenerate_draft(email_id: str, request: RegenerateRequest):
    try:
        new_draft = await ai_engine.regenerate_draft(email_id, request.custom_instructions)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE emails SET ai_draft = ? WHERE id = ?", (new_draft, email_id))
        await db.execute(
            "INSERT INTO activity_log (id, email_id, action, actor, details) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), email_id, "draft_regenerated", "human",
             f"Custom: {request.custom_instructions}" if request.custom_instructions else "Regenerated")
        )
        await db.commit()
    return {"draft": new_draft}


@router.post("/simulate")
async def simulate_email(request: ManualEmailRequest):
    email_id = str(uuid.uuid4())
    message_id = f"<simulate-{email_id}@neuralinbox>"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO emails (id, message_id, sender_name, sender_email, subject, body_text, received_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'processing')
        """, (email_id, message_id, request.sender_name, request.sender_email,
              request.subject, request.body, datetime.utcnow().isoformat()))
        await db.commit()
    try:
        result = await ai_engine.process_email(
            email_id=email_id,
            sender_name=request.sender_name,
            sender_email=request.sender_email,
            subject=request.subject,
            body=request.body
        )
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                UPDATE emails SET department=?, intent=?, sentiment=?, urgency=?,
                confidence_score=?, ai_draft=?, ai_reasoning=?, status='pending_approval'
                WHERE id=?
            """, (result["department"], result["intent"], result["sentiment"],
                  result["urgency"], result["confidence"], result["draft"],
                  result["reasoning"], email_id))
            await db.commit()
    except Exception as e:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE emails SET status='failed' WHERE id=?", (email_id,))
            await db.commit()
        raise HTTPException(status_code=500, detail=str(e))
    return {"email_id": email_id, "result": result}
