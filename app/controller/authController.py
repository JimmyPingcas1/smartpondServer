from fastapi import HTTPException

from ..services import authService
from ..model.authModel import (
    LoginRequest,
    RegisterRequest,
    ForgotPasswordRequest,
    VerifyResetCodeRequest,
    ResetPasswordRequest,
    ChangeEmailRequest,
)


async def login(credentials: LoginRequest):
    try:
        return await authService.login_user(
            credentials.email,
            credentials.password
        )
    except ValueError as e:
        raise HTTPException(
            status_code=401,
            detail=str(e)
        )


async def register(credentials: RegisterRequest):
    try:
        return await authService.register_user(
            credentials.email,
            credentials.password,
            credentials.name
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )


async def forgot_password(request: ForgotPasswordRequest):
    try:
        return await authService.forgot_password(
            request.email
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )


async def verify_reset_code(request: VerifyResetCodeRequest):
    try:
        return await authService.verify_reset_code(
            request.email,
            request.token
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )


async def reset_password(request: ResetPasswordRequest):
    try:
        return await authService.reset_password(
            request.email,
            request.token,
            request.new_password
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )


# CHANGE EMAIL
# ============================================================
async def change_email(
    request: ChangeEmailRequest,
    user_id: str
):
    try:
        return await authService.change_email(
            user_id=user_id,
            new_email=request.new_email
        )

    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    