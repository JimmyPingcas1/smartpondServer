import smtplib
from email.mime.text import MIMEText

from ..config.config import settings


def send_reset_code_email(email: str, code: str):
    subject = "SMARTPOND - Password Reset Code"

    body = (
        f"Hello,\n\n"
        f"Your SMARTPOND password reset code is: {code}\n\n"
        "This code will expire in 10 minutes.\n\n"
        "If you did not request this password reset, please ignore this email.\n\n"
        "Regards,\n"
        "SMARTPOND"
    )

    message = MIMEText(body)
    message["Subject"] = subject
    message["From"] = settings.SMTP_FROM
    message["To"] = email

    if (
        settings.SMTP_HOST
        and settings.SMTP_USER
        and settings.SMTP_PASS
    ):
        try:
            with smtplib.SMTP_SSL(
                settings.SMTP_HOST,
                settings.SMTP_PORT
            ) as server:
                server.login(
                    settings.SMTP_USER,
                    settings.SMTP_PASS
                )

                server.sendmail(
                    settings.SMTP_FROM,
                    [email],
                    message.as_string()
                )

        except Exception as e:
            print(f"Failed to send reset code email: {e}")

    else:
        print(f"[DEV] Password reset code for {email}: {code}")