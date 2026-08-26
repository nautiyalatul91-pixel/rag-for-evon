import os
import smtplib
import urllib.request
import urllib.error
import json
from email.mime.text import MIMEText
from app.config import logger

class OutreachService:
    def __init__(self):
        self.provider = os.getenv("OUTREACH_EMAIL_PROVIDER", "smtp").lower().strip()
        
        # SMTP configuration
        self.smtp_host = os.getenv("SMTP_HOST", "").strip()
        self.smtp_port_str = os.getenv("SMTP_PORT", "587").strip()
        self.smtp_username = os.getenv("SMTP_USERNAME", "").strip()
        self.smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
        self.smtp_use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
        self.smtp_from_email = os.getenv("SMTP_FROM_EMAIL", "").strip() or self.smtp_username

        # SendGrid configuration
        self.sendgrid_api_key = os.getenv("SENDGRID_API_KEY", "").strip()
        self.sendgrid_from_email = os.getenv("SENDGRID_FROM_EMAIL", "").strip()

    def is_configured(self) -> bool:
        """
        Checks whether the selected email provider is correctly configured.
        Returns False if credentials are empty or use default placeholder values,
        triggering the dry-run fallback.
        """
        def is_placeholder(val: str) -> bool:
            if not val:
                return True
            placeholders = ["placeholder", "your_", "example.com", "myusername", "mypassword"]
            v_low = val.lower()
            return any(p in v_low for p in placeholders)

        if self.provider == "sendgrid":
            return not is_placeholder(self.sendgrid_api_key) and not is_placeholder(self.sendgrid_from_email)
        else:  # default to smtp
            return (
                not is_placeholder(self.smtp_host) and
                not is_placeholder(self.smtp_username) and
                not is_placeholder(self.smtp_password)
            )

    def send_email(self, recipient: str, subject: str, body: str) -> dict:
        """
        Deliver the outreach email.
        If credentials are not configured, runs in dry-run mode.
        Returns a dict: {"success": True, "provider": str, "dry_run": bool}
        """
        recipient = recipient.strip()
        if not recipient:
            raise ValueError("Recipient email address cannot be empty.")

        # Determine if we should fall back to dry run
        if not self.is_configured():
            logger.info(
                "--- DRY RUN EMAIL TRANSMISSION ---\n"
                "To: %s\n"
                "Subject: %s\n"
                "Provider: %s\n"
                "Body:\n%s\n"
                "----------------------------------",
                recipient, subject, self.provider, body
            )
            return {
                "success": True,
                "provider": f"{self.provider} (Dry-run)",
                "dry_run": True
            }

        if self.provider == "sendgrid":
            return self._send_sendgrid(recipient, subject, body)
        else:
            return self._send_smtp(recipient, subject, body)

    def _send_smtp(self, recipient: str, subject: str, body: str) -> dict:
        logger.info("Outreach Service: Connecting to SMTP server %s:%s...", self.smtp_host, self.smtp_port_str)
        try:
            port = int(self.smtp_port_str)
        except ValueError:
            port = 587

        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = self.smtp_from_email
        msg['To'] = recipient

        try:
            # Connect to SMTP server
            # Use SSL if port is 465, else use standard SMTP with TLS
            if port == 465:
                server = smtplib.SMTP_SSL(self.smtp_host, port, timeout=10)
            else:
                server = smtplib.SMTP(self.smtp_host, port, timeout=10)
                if self.smtp_use_tls:
                    server.starttls()
            
            server.login(self.smtp_username, self.smtp_password)
            server.sendmail(self.smtp_from_email, [recipient], msg.as_string())
            server.quit()
            
            logger.info("Outreach Service: SMTP email successfully sent to %s", recipient)
            return {
                "success": True,
                "provider": "smtp",
                "dry_run": False
            }
        except Exception as e:
            logger.error("Outreach Service: SMTP email delivery failed: %s", e, exc_info=True)
            raise e

    def _send_sendgrid(self, recipient: str, subject: str, body: str) -> dict:
        logger.info("Outreach Service: Sending email via SendGrid API to %s...", recipient)
        
        payload = {
            "personalizations": [
                {
                    "to": [{"email": recipient}],
                    "subject": subject
                }
            ],
            "from": {"email": self.sendgrid_from_email},
            "content": [
                {
                    "type": "text/plain",
                    "value": body
                }
            ]
        }
        
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data=data,
            headers={
                "Authorization": f"Bearer {self.sendgrid_api_key}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                status_code = response.getcode()
                if status_code in (200, 201, 202):
                    logger.info("Outreach Service: SendGrid API email successfully sent to %s (Status: %s)", recipient, status_code)
                    return {
                        "success": True,
                        "provider": "sendgrid",
                        "dry_run": False
                    }
                else:
                    raise Exception(f"SendGrid responded with status code: {status_code}")
        except urllib.error.HTTPError as he:
            err_body = he.read().decode('utf-8')
            logger.error("Outreach Service: SendGrid API HTTP error: %s - Response: %s", he, err_body)
            raise Exception(f"SendGrid API error: {he.reason} - Details: {err_body}")
        except Exception as e:
            logger.error("Outreach Service: SendGrid API connection failed: %s", e, exc_info=True)
            raise e

# Global outreach delivery instance
outreach_service = OutreachService()
