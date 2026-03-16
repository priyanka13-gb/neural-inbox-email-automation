from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import aiosqlite
import os
from datetime import datetime
from services.smtp_sender import SMTPSender

router = APIRouter()
DB_PATH = os.getenv("DB_PATH", "./neuralinbox.db")


class EmailSettings(BaseModel):
    imap_host: Optional[str] = None
    imap_port: Optional[int] = 993
    imap_user: Optional[str] = None
    imap_password: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    company_name: Optional[str] = None
    check_interval: Optional[int] = 60
    auto_send_enabled: Optional[bool] = False


class DepartmentUpdate(BaseModel):
    name: str
    description: Optional[str] = None
    tone: Optional[str] = "professional"
    signature: Optional[str] = None
    auto_send_threshold: Optional[float] = 0.95
    color: Optional[str] = "#6366f1"


@router.get("")
async def get_settings():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM email_settings WHERE id = 1") as cur:
            row = await cur.fetchone()
    s = dict(row) if row else {}
    if s.get("imap_password"):
        s["imap_password"] = "••••••••"
    if s.get("smtp_password"):
        s["smtp_password"] = "••••••••"
    return s


@router.put("")
async def update_settings(settings: EmailSettings):
    async with aiosqlite.connect(DB_PATH) as db:
        data = settings.dict(exclude_none=True)
        data["updated_at"] = datetime.utcnow().isoformat()
        sets = ", ".join([f"{k} = ?" for k in data.keys()])
        await db.execute(f"UPDATE email_settings SET {sets} WHERE id = 1", list(data.values()))
        await db.commit()
    return {"success": True}


@router.post("/test-smtp")
async def test_smtp(payload: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM email_settings WHERE id = 1") as cur:
            settings = dict(await cur.fetchone())
    sender = SMTPSender()
    try:
        await sender.send_test_email(payload.get("test_email", settings["smtp_user"]), settings)
        return {"success": True, "message": "Test email sent!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/departments")
async def get_departments():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM departments") as cur:
            return [dict(r) for r in await cur.fetchall()]


@router.put("/departments/{dept_id}")
async def update_department(dept_id: str, update: DepartmentUpdate):
    async with aiosqlite.connect(DB_PATH) as db:
        data = update.dict(exclude_none=True)
        sets = ", ".join([f"{k} = ?" for k in data.keys()])
        await db.execute(f"UPDATE departments SET {sets} WHERE id = ?", list(data.values()) + [dept_id])
        await db.commit()
    return {"success": True}
