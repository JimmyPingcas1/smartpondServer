import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # =========================
    # MongoDB 
    # =========================
    MONGO_URI = os.getenv("MONGO_URI")
    
    MONGO_DB = os.getenv("MONGO_DB")

    # =========================
    # PhilSMS
    # =========================
    PHILSMS_TOKEN = os.getenv(
        "PHILSMS_TOKEN"
    )

    PHILSMS_SENDER_ID = os.getenv(
        "PHILSMS_SENDER_ID",
        "PhilSMS"
    )

    OPENAI_API_KEY = os.getenv(
        "OPENAI_API_KEY"
    )

    OPENAI_MODEL = os.getenv(
        "OPENAI_MODEL",
        "gpt-5.4-nano"
    )

    # =========================
    # Authentication
    # =========================

    JWT_SECRET_KEY = os.getenv(
        "JWT_SECRET_KEY"
    )

    JWT_ALGORITHM = os.getenv(
        "JWT_ALGORITHM",
        "HS256"
    )

    JWT_EXPIRE_DAYS = int(
        os.getenv("JWT_EXPIRE_DAYS", "7")
    )

     # =========================
    # Admin
    # =========================


    # =========================
    # SMTP Email
    # =========================

    SMTP_HOST = os.getenv(
        "SMTP_HOST"
    )

    SMTP_PORT = int(
        os.getenv("SMTP_PORT", "465")
    )

    SMTP_USER = os.getenv(
        "SMTP_USER"
    )

    SMTP_PASS = os.getenv(
        "SMTP_PASS"
    )

    SMTP_FROM = os.getenv(
        "SMTP_FROM"
    )

     # =========================
    # Password Reset
    # =========================

    RESET_CODE_EXPIRE_MINUTES = int(
        os.getenv("RESET_CODE_EXPIRE_MINUTES", "10")
    )

settings = Settings()