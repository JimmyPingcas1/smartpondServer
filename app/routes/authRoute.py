from fastapi import APIRouter, Request, Depends

from ..controller import authController
from ..middleware.rateLimiter import limiter
from ..middleware.authMiddleware import get_current_user_id

from ..model.authModel import (
    LoginRequest,
    RegisterRequest,
    ForgotPasswordRequest,
    VerifyResetCodeRequest,
    ResetPasswordRequest,
    ChangeEmailRequest,
)


router = APIRouter()


# VERIFY RESET CODE
# ============================================================
@router.post("/api/v1/verify-reset-code")
@limiter.limit("5/minute")
async def verify_reset_code(
    request: Request,
    data: VerifyResetCodeRequest
):
    return await authController.verify_reset_code(data)


# LOGIN
# ============================================================
@router.post("/api/v1/login")
@limiter.limit("5/minute")
async def login(
    request: Request,
    credentials: LoginRequest
):
    return await authController.login(credentials)


# FORGOT PASSWORD
# ============================================================
@router.post("/api/v1/forgot-password")
@limiter.limit("3/minute")
async def forgot_password(
    request: Request,
    data: ForgotPasswordRequest
):
    return await authController.forgot_password(data)


# RESET PASSWORD
# ============================================================
@router.post("/api/v1/reset-password")
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    data: ResetPasswordRequest
):
    return await authController.reset_password(data)


# REGISTER
# ============================================================
@router.post("/api/v1/register")
@limiter.limit("5/minute")
async def register(
    request: Request,
    credentials: RegisterRequest
):
    return await authController.register(credentials)


# CHANGE EMAIL
# ============================================================
@router.put("/api/v1/change-email")
@limiter.limit("5/minute")
async def change_email(
    request: Request,
    data: ChangeEmailRequest,
    user_id: str = Depends(get_current_user_id)
):
    return await authController.change_email(
        data,
        user_id
    )

