import bcrypt
import jwt
import random

from datetime import datetime, timedelta, timezone
from bson import ObjectId

from ..config.config import settings
from ..db import user_collection
from ..providers.smpt import send_reset_code_email


# PASSWORD FUNCTIONS
def hash_password(password: str) -> str:
    password_bytes = password.encode("utf-8")

    hashed = bcrypt.hashpw(
        password_bytes,
        bcrypt.gensalt()
    )

    return hashed.decode("utf-8")


def verify_password(
    plain_password: str,
    stored_password: str
) -> bool:
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            stored_password.encode("utf-8")
        )
    except (ValueError, TypeError):
        return False


def is_bcrypt_hash(password: str) -> bool:
    if not isinstance(password, str):
        return False

    return password.startswith(
        ("$2a$", "$2b$", "$2y$")
    )


# JWT
def create_access_token(
    user_id: str,
    role: str = "user",
    expires_delta: timedelta | None = None
) -> str:

    if expires_delta is None:
        expires_delta = timedelta(
            days=settings.JWT_EXPIRE_DAYS
        )

    expire = datetime.now(timezone.utc) + expires_delta

    payload = {
        "user_id": str(user_id),
        "role": str(role),
        "exp": expire,
    }

    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM
    )


# LOGIN
async def login_user(
    email: str,
    password: str
):

    email = email.strip().lower()

    # FIND USER
    user = await user_collection.find_one({
        "email": email
    })

    if not user:
        raise ValueError(
            "Invalid email or password"
        )

    # CHECK ACCOUNT STATUS
    account_status = str(
        user.get("status", "Active")
    ).strip().lower()

    if account_status != "active":
        raise ValueError(
            "Your account is inactive. "
            "Please contact an administrator."
        )

    # GET STORED PASSWORD
    stored_password = user.get("password")

    if not stored_password:
        raise ValueError(
            "Invalid email or password"
        )

    # VERIFY PASSWORD
    password_valid = False

    if is_bcrypt_hash(stored_password):

        password_valid = verify_password(
            password,
            stored_password
        )

    else:

        # Temporary support for old plain-text passwords
        if stored_password == password:

            password_valid = True

            new_hash = hash_password(password)

            await user_collection.update_one(
                {"_id": user["_id"]},
                {
                    "$set": {
                        "password": new_hash,
                        "updated_at": datetime.now(timezone.utc)
                    }
                }
            )

            print(
                f"Password migrated to bcrypt for: {email}"
            )

    if not password_valid:
        raise ValueError(
            "Invalid email or password"
        )

    # GET ROLE FROM DATABASE
    user_role = str(
        user.get("role", "user")
    ).strip().lower()

    # CREATE JWT
    token = create_access_token(
        str(user["_id"]),
        role=user_role
    )

    return {
        "token": token,
        "user": {
            "id": str(user["_id"]),
            "email": user.get("email"),
            "name": user.get("name", ""),
            "location": user.get("location", ""),
            "profile_image_url": user.get(
                "profile_image_url",
                ""
            ),
            "role": user_role,
            "status": user.get(
                "status",
                "Active"
            ),
        },
    }



# REGISTER
async def register_user(
    email: str,
    password: str,
    name: str
):

    email = email.strip().lower()
    name = name.strip()

    # CHECK EXISTING USER
    existing_user = await user_collection.find_one({
        "email": email
    })

    if existing_user:
        raise ValueError(
            "Email already registered"
        )

    # HASH PASSWORD
    hashed_password = hash_password(password)

    now = datetime.now(timezone.utc)

    # CREATE USER DOCUMENT
    user_doc = {
        "name": name,
        "location": "",
        "profile_image_url": "",
        "email": email,
        "password": hashed_password,
        "status": "Active",
        "role": "user",
        "code": None,
        "expires_at": None,
        "created_at": now,
        "updated_at": now,
    }

    # INSERT USER
    result = await user_collection.insert_one(
        user_doc
    )

    # CREATE JWT
    token = create_access_token(
        str(result.inserted_id),
        role="user"
    )

    return {
        "token": token,
        "user": {
            "id": str(result.inserted_id),
            "email": email,
            "name": name,
            "location": "",
            "profile_image_url": "",
            "role": "user",
            "status": "Active",
        },
    }


# FORGOT PASSWORD
async def forgot_password(
    email: str
):

    email = email.strip().lower()

    # FIND USER
    user = await user_collection.find_one({
        "email": email
    })

    if not user:
        raise ValueError(
            "Email not found"
        )

    # GENERATE RESET CODE
    code = str(
        random.randint(100000, 999999)
    )

    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(
            minutes=settings.RESET_CODE_EXPIRE_MINUTES
        )
    )

    # SAVE RESET CODE
    await user_collection.update_one(
        {"email": email},
        {
            "$set": {
                "code": code,
                "expires_at": expires_at,
            }
        }
    )

    # SEND EMAIL
    send_reset_code_email(
        email,
        code
    )

    return {
        "message": "Password reset code sent to your email"
    }


# VERIFY RESET CODE
async def verify_reset_code(
    email: str,
    code: str
):

    email = email.strip().lower()

    # FIND USER
    user = await user_collection.find_one({
        "email": email
    })

    if not user:
        raise ValueError(
            "Invalid or expired code"
        )

    # CHECK RESET CODE EXISTS
    if (
        "code" not in user
        or "expires_at" not in user
    ):
        raise ValueError(
            "Invalid or expired code"
        )

    # CHECK CODE
    if user["code"] != code:
        raise ValueError(
            "Invalid code"
        )

    # CHECK EXPIRATION
    expires_at = user["expires_at"]

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(
            tzinfo=timezone.utc
        )

    if datetime.now(timezone.utc) > expires_at:
        raise ValueError(
            "Code expired"
        )

    return {
        "success": True,
        "message": "Code is valid"
    }


# RESET PASSWORD
async def reset_password(
    email: str,
    code: str,
    new_password: str
):

    email = email.strip().lower()

    # FIND USER
    user = await user_collection.find_one({
        "email": email
    })

    if not user:
        raise ValueError(
            "Invalid or expired code"
        )

    # CHECK RESET CODE
    if (
        "code" not in user
        or "expires_at" not in user
    ):
        raise ValueError(
            "Invalid or expired code"
        )

    if user["code"] != code:
        raise ValueError(
            "Invalid code"
        )

    # CHECK EXPIRATION
    expires_at = user["expires_at"]

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(
            tzinfo=timezone.utc
        )

    if datetime.now(timezone.utc) > expires_at:
        raise ValueError(
            "Code expired"
        )

    # HASH NEW PASSWORD
    hashed_password = hash_password(
        new_password
    )

    # UPDATE PASSWORD
    result = await user_collection.update_one(
        {"email": email},
        {
            "$set": {
                "password": hashed_password,
                "updated_at": datetime.now(timezone.utc),
            },
            "$unset": {
                "code": "",
                "expires_at": "",
            },
        }
    )

    if result.modified_count == 0:
        raise ValueError(
            "Failed to update password"
        )

    return {
        "success": True,
        "message": "Password has been reset successfully"
    }


# CHANGE EMAIL
async def change_email(
    user_id: str,
    new_email: str
):

    new_email = new_email.strip().lower()

    # CHECK IF NEW EMAIL IS ALREADY USED
    existing_user = await user_collection.find_one({
        "email": new_email
    })

    if existing_user:
        if str(existing_user["_id"]) != str(user_id):
            raise ValueError(
                "Email is already registered"
            )

    # FIND CURRENT USER
    user = await user_collection.find_one({
        "_id": ObjectId(user_id)
    })

    if not user:
        raise ValueError(
            "User account not found"
        )

    # UPDATE EMAIL
    result = await user_collection.update_one(
        {
            "_id": ObjectId(user_id)
        },
        {
            "$set": {
                "email": new_email,
                "updated_at": datetime.now(timezone.utc),
            }
        }
    )

    if result.modified_count == 0:
        raise ValueError(
            "Email was not changed"
        )
        

    return {
        "success": True,
        "message": "Email updated successfully",
        "email": new_email,
    }


