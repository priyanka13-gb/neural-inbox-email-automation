from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import uvicorn
from datetime import datetime

from routes import emails, knowledge, dashboard, auth, settings
from services.imap_listener import IMAPListener
from services.database import init_db
from utils.logger import logger

imap_listener = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("NeuralInbox starting...")
    await init_db()
    global imap_listener
    imap_listener = IMAPListener()
    asyncio.create_task(imap_listener.start_listening())
    yield
    if imap_listener:
        await imap_listener.stop()

app = FastAPI(title="NeuralInbox API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(emails.router, prefix="/api/emails", tags=["Emails"])
app.include_router(knowledge.router, prefix="/api/knowledge", tags=["Knowledge Base"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(settings.router, prefix="/api/settings", tags=["Settings"])

@app.get("/")
async def root():
    return {"name": "NeuralInbox", "version": "1.0.0", "status": "running", "timestamp": datetime.utcnow().isoformat()}

@app.get("/api/health")
async def health():
    return {"status": "healthy", "imap_active": imap_listener is not None}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
