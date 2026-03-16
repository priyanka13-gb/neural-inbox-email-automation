import imaplib
import email
from email.header import decode_header
import asyncio
import uuid
import os
from datetime import datetime
import aiosqlite

from services.ai_engine import AIEngine
from services.smtp_sender import SMTPSender
from utils.logger import logger

DB_PATH = os.getenv("DB_PATH", "./neuralinbox.db")


def decode_mime_words(s):
    if not s:
        return ""
    decoded_parts = decode_header(s)
    result = []
    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(encoding or "utf-8", errors="replace"))
        else:
            result.append(part)
    return " ".join(result)


def extract_body(msg):
    text_body = ""
    html_body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in content_disposition:
                continue
            if content_type == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                text_body = part.get_payload(decode=True).decode(charset, errors="replace")
            elif content_type == "text/html":
                charset = part.get_content_charset() or "utf-8"
                html_body = part.get_payload(decode=True).decode(charset, errors="replace")
    else:
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        if payload:
            if msg.get_content_type() == "text/html":
                html_body = payload.decode(charset, errors="replace")
            else:
                text_body = payload.decode(charset, errors="replace")
    return text_body, html_body


class IMAPListener:
    def __init__(self):
        self.running = False
        self.ai_engine = AIEngine()
        self.smtp_sender = SMTPSender()
        self._processed_ids = set()

    async def get_settings(self):
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM email_settings WHERE id = 1") as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def start_listening(self):
        self.running = True
        logger.info("IMAP listener started")
        while self.running:
            try:
                settings = await self.get_settings()
                if settings and settings.get("imap_host") and settings.get("imap_user"):
                    await self.fetch_and_process(settings)
                else:
                    logger.info("IMAP not configured yet")
                interval = settings.get("check_interval", 60) if settings else 60
                await asyncio.sleep(interval)
            except Exception as e:
                logger.error(f"IMAP listener error: {e}")
                await asyncio.sleep(30)

    async def stop(self):
        self.running = False

    async def fetch_and_process(self, settings):
        try:
            mail = imaplib.IMAP4_SSL(settings["imap_host"], settings.get("imap_port", 993))
            mail.login(settings["imap_user"], settings["imap_password"])
            mail.select("INBOX")
            _, message_numbers = mail.search(None, "UNSEEN")
            ids = message_numbers[0].split()
            if not ids:
                return
            logger.info(f"Found {len(ids)} new emails")
            for num in ids:
                try:
                    _, msg_data = mail.fetch(num, "(RFC822)")
                    raw_email = msg_data[0][1]
                    msg = email.message_from_bytes(raw_email)
                    message_id = msg.get("Message-ID", str(uuid.uuid4()))
                    if message_id in self._processed_ids:
                        continue
                    await self.process_email(msg, message_id, settings)
                    self._processed_ids.add(message_id)
                    mail.store(num, "+FLAGS", "\\Seen")
                except Exception as e:
                    logger.error(f"Error processing email {num}: {e}")
            mail.logout()
        except Exception as e:
            logger.error(f"IMAP connection error: {e}")

    async def process_email(self, msg, message_id, settings):
        sender_raw = msg.get("From", "")
        subject = decode_mime_words(msg.get("Subject", "(No Subject)"))
        text_body, html_body = extract_body(msg)
        sender_name = ""
        sender_email = ""
        if "<" in sender_raw:
            parts = sender_raw.split("<")
            sender_name = decode_mime_words(parts[0].strip().strip('"'))
            sender_email = parts[1].rstrip(">").strip()
        else:
            sender_email = sender_raw.strip()

        body = text_body or html_body[:2000]
        email_id = str(uuid.uuid4())

        logger.info(f"Processing: '{subject}' from {sender_email}")

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT OR IGNORE INTO emails
                (id, message_id, sender_name, sender_email, subject, body_text, body_html, received_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'processing')
            """, (email_id, message_id, sender_name, sender_email, subject, body, html_body,
                  datetime.utcnow().isoformat()))
            await db.commit()

        try:
            ai_result = await self.ai_engine.process_email(
                email_id=email_id,
                sender_name=sender_name,
                sender_email=sender_email,
                subject=subject,
                body=body
            )

            async with aiosqlite.connect(DB_PATH) as db:
                status = "pending_approval"
                auto_send_threshold = ai_result.get("auto_send_threshold", 0.95)
                if (settings.get("auto_send_enabled") and
                        ai_result["confidence"] >= auto_send_threshold):
                    status = "auto_sent"

                await db.execute("""
                    UPDATE emails SET
                        department = ?, intent = ?, sentiment = ?, urgency = ?,
                        confidence_score = ?, ai_draft = ?, ai_reasoning = ?, status = ?
                    WHERE id = ?
                """, (ai_result["department"], ai_result["intent"], ai_result["sentiment"],
                      ai_result["urgency"], ai_result["confidence"], ai_result["draft"],
                      ai_result["reasoning"], status, email_id))
                await db.commit()
                await self._log_activity(db, email_id, "ai_processed", "ai",
                    f"Intent: {ai_result['intent']}, Confidence: {ai_result['confidence']:.0%}")

            if status == "auto_sent":
                await self.smtp_sender.send_reply(
                    to_email=sender_email,
                    to_name=sender_name,
                    subject=subject,
                    reply_body=ai_result["draft"],
                    original_message_id=message_id,
                    settings=settings
                )
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE emails SET replied_at = ?, auto_sent = 1 WHERE id = ?",
                        (datetime.utcnow().isoformat(), email_id)
                    )
                    await db.commit()
                    await self._log_activity(db, email_id, "auto_sent", "ai", "Auto-sent based on confidence score")
                logger.info(f"Auto-sent reply to {sender_email}")
            else:
                logger.info(f"Queued for approval: {subject}")

        except Exception as e:
            logger.error(f"AI processing error: {e}")
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE emails SET status = 'failed' WHERE id = ?", (email_id,))
                await db.commit()

    async def _log_activity(self, db, email_id, action, actor, details):
        await db.execute(
            "INSERT INTO activity_log (id, email_id, action, actor, details) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), email_id, action, actor, details)
        )
        await db.commit()
