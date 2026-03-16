import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from utils.logger import logger


class SMTPSender:
    async def send_reply(self, to_email, to_name, subject, reply_body, original_message_id, settings):
        smtp_host = settings.get("smtp_host")
        smtp_port = settings.get("smtp_port", 587)
        smtp_user = settings.get("smtp_user")
        smtp_password = settings.get("smtp_password")
        company_name = settings.get("company_name", "Our Company")

        if not all([smtp_host, smtp_user, smtp_password]):
            raise ValueError("SMTP settings not configured")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Re: {subject}" if not subject.startswith("Re:") else subject
        msg["From"] = f"{company_name} <{smtp_user}>"
        msg["To"] = f"{to_name} <{to_email}>" if to_name else to_email
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid()

        if original_message_id:
            msg["In-Reply-To"] = original_message_id
            msg["References"] = original_message_id

        msg.attach(MIMEText(reply_body, "plain"))

        html_body = reply_body.replace('\n', '<br>')
        html = f"""<html><body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px;">{html_body}</body></html>"""
        msg.attach(MIMEText(html, "html"))

        try:
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_user, to_email, msg.as_string())
            logger.info(f"Reply sent to {to_email}")
            return True
        except Exception as e:
            logger.error(f"SMTP send error: {e}")
            raise

    async def send_test_email(self, to_email, settings):
        return await self.send_reply(
            to_email=to_email,
            to_name="Test",
            subject="NeuralInbox SMTP Test",
            reply_body="Your SMTP settings are working correctly!\n\nNeuralInbox is ready to send emails.",
            original_message_id=None,
            settings=settings
        )
