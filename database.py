import aiosqlite
import os
from utils.logger import logger

DB_PATH = os.getenv("DB_PATH", "./neuralinbox.db")

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS emails (
                id TEXT PRIMARY KEY,
                message_id TEXT UNIQUE,
                thread_id TEXT,
                sender_name TEXT,
                sender_email TEXT NOT NULL,
                recipient TEXT,
                subject TEXT,
                body_text TEXT,
                body_html TEXT,
                received_at TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                department TEXT,
                intent TEXT,
                sentiment TEXT,
                urgency TEXT DEFAULT 'normal',
                confidence_score REAL DEFAULT 0.0,
                ai_draft TEXT,
                ai_reasoning TEXT,
                human_edited INTEGER DEFAULT 0,
                final_reply TEXT,
                replied_at TEXT,
                auto_sent INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS knowledge_docs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                file_type TEXT,
                content TEXT NOT NULL,
                chunk_index INTEGER DEFAULT 0,
                embedding TEXT,
                department TEXT DEFAULT 'general',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS departments (
                id TEXT PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                tone TEXT DEFAULT 'professional',
                signature TEXT,
                auto_send_threshold REAL DEFAULT 0.95,
                color TEXT DEFAULT '#6366f1',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                role TEXT DEFAULT 'agent',
                department TEXT,
                password_hash TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS email_settings (
                id INTEGER PRIMARY KEY DEFAULT 1,
                imap_host TEXT,
                imap_port INTEGER DEFAULT 993,
                imap_user TEXT,
                imap_password TEXT,
                smtp_host TEXT,
                smtp_port INTEGER DEFAULT 587,
                smtp_user TEXT,
                smtp_password TEXT,
                company_name TEXT DEFAULT 'Our Company',
                check_interval INTEGER DEFAULT 60,
                auto_send_enabled INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS activity_log (
                id TEXT PRIMARY KEY,
                email_id TEXT,
                action TEXT NOT NULL,
                actor TEXT DEFAULT 'ai',
                details TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            INSERT OR IGNORE INTO departments (id, name, description, tone, signature, color) VALUES
                ('dept_support', 'Support', 'Customer support and technical issues', 'friendly', 'Best regards,\nSupport Team', '#22c55e'),
                ('dept_billing', 'Billing', 'Payments, invoices, and refunds', 'professional', 'Kind regards,\nBilling Team', '#f59e0b'),
                ('dept_sales', 'Sales', 'New business and partnerships', 'enthusiastic', 'Looking forward to hearing from you,\nSales Team', '#6366f1'),
                ('dept_general', 'General', 'General inquiries', 'professional', 'Best regards,\nTeam', '#64748b');

            INSERT OR IGNORE INTO email_settings (id) VALUES (1);
        """)
        await db.commit()
    logger.info("Database initialized")

async def get_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db
