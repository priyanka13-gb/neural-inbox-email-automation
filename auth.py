from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import hashlib
import uuid
import aiosqlite
import os
import jwt
from datetime import datetime, timedelta

router = APIRouter()
DB_PATH = os.getenv("DB_PATH", "./neuralinbox.db")
SECRET = os.getenv("JWT_SECRET", "neuralinbox-secret-2024")


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    name: str
    password: str
    role: str = "agent"


def hash_password(p):
    return hashlib.sha256(p.encode()).hexdigest()


def make_token(user_id, email, role):
    payload = {"sub": user_id, "email": email, "role": role,
               "exp": datetime.utcnow() + timedelta(days=7)}
    return jwt.encode(payload, SECRET, algorithm="HS256")


@router.post("/register")
async def register(req: RegisterRequest):
    user_id = str(uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO users (id, email, name, role, password_hash) VALUES (?, ?, ?, ?, ?)",
                (user_id, req.email, req.name, req.role, hash_password(req.password))
            )
            await db.commit()
        except Exception:
            raise HTTPException(status_code=400, detail="Email already exists")
    return {"token": make_token(user_id, req.email, req.role),
            "user": {"id": user_id, "email": req.email, "name": req.name, "role": req.role}}


@router.post("/login")
async def login(req: LoginRequest):
    if req.email == "admin@demo.com" and req.password == "demo123":
        demo_id = "demo-admin-001"
        return {"token": make_token(demo_id, req.email, "admin"),
                "user": {"id": demo_id, "email": req.email, "name": "Demo Admin", "role": "admin"}}

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE email = ?", (req.email,)) as cur:
            user = await cur.fetchone()

    if not user or user["password_hash"] != hash_password(req.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    u = dict(user)
    return {"token": make_token(u["id"], u["email"], u["role"]),
            "user": {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"]}}
