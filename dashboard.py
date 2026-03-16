from fastapi import APIRouter
import aiosqlite
import os
from datetime import datetime, timedelta

router = APIRouter()
DB_PATH = os.getenv("DB_PATH", "./neuralinbox.db")


@router.get("/stats")
async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        stats = {}

        async with db.execute("SELECT COUNT(*) as c FROM emails") as cur:
            stats["total_emails"] = (await cur.fetchone())["c"]

        async with db.execute("SELECT status, COUNT(*) as c FROM emails GROUP BY status") as cur:
            stats["by_status"] = {row["status"]: row["c"] for row in await cur.fetchall()}

        async with db.execute("SELECT COUNT(*) as c FROM emails WHERE auto_sent = 1") as cur:
            auto_sent = (await cur.fetchone())["c"]
        stats["auto_sent"] = auto_sent
        stats["auto_send_rate"] = round(auto_sent / max(stats["total_emails"], 1) * 100, 1)

        async with db.execute(
            "SELECT department, COUNT(*) as c FROM emails WHERE department IS NOT NULL GROUP BY department"
        ) as cur:
            stats["by_department"] = [dict(row) for row in await cur.fetchall()]

        async with db.execute(
            "SELECT intent, COUNT(*) as c FROM emails WHERE intent IS NOT NULL GROUP BY intent ORDER BY c DESC LIMIT 8"
        ) as cur:
            stats["by_intent"] = [dict(row) for row in await cur.fetchall()]

        async with db.execute(
            "SELECT sentiment, COUNT(*) as c FROM emails WHERE sentiment IS NOT NULL GROUP BY sentiment"
        ) as cur:
            stats["by_sentiment"] = [dict(row) for row in await cur.fetchall()]

        async with db.execute(
            "SELECT urgency, COUNT(*) as c FROM emails WHERE urgency IS NOT NULL GROUP BY urgency"
        ) as cur:
            stats["by_urgency"] = [dict(row) for row in await cur.fetchall()]

        seven_days_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
        async with db.execute(
            "SELECT DATE(received_at) as day, COUNT(*) as c FROM emails WHERE received_at > ? GROUP BY day ORDER BY day",
            (seven_days_ago,)
        ) as cur:
            stats["daily_volume"] = [dict(row) for row in await cur.fetchall()]

        async with db.execute("SELECT COUNT(*) as c FROM emails WHERE status = 'pending_approval'") as cur:
            stats["pending_approval"] = (await cur.fetchone())["c"]

        async with db.execute("SELECT AVG(confidence_score) as avg FROM emails WHERE confidence_score > 0") as cur:
            row = await cur.fetchone()
            stats["avg_confidence"] = round((row["avg"] or 0) * 100, 1)

        async with db.execute("SELECT COUNT(DISTINCT name) as c FROM knowledge_docs") as cur:
            stats["kb_documents"] = (await cur.fetchone())["c"]

        async with db.execute(
            "SELECT al.*, e.subject, e.sender_email FROM activity_log al LEFT JOIN emails e ON al.email_id = e.id ORDER BY al.created_at DESC LIMIT 10"
        ) as cur:
            stats["recent_activity"] = [dict(row) for row in await cur.fetchall()]

    return stats
