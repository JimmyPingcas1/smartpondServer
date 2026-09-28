from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):

    email: EmailStr
    password: str = Field(min_length=8)
    name: str = Field(min_length=1)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value):

        if not any(char.isdigit() for char in value):
            raise ValueError(
                "Password must contain at least one number"
            )

        return value


class LoginRequest(BaseModel):

    email: EmailStr
    password: str = Field(min_length=1)
    name: str = ""


class LoginResponse(BaseModel):

    token: str
    user: dict


class ForgotPasswordRequest(BaseModel):

    email: EmailStr


class VerifyResetCodeRequest(BaseModel):

    email: EmailStr
    token: str

class ChangeEmailRequest(BaseModel):
    new_email: EmailStr

class ResetPasswordRequest(BaseModel):

    email: EmailStr
    token: str
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value):

        if not any(char.isdigit() for char in value):
            raise ValueError(
                "Password must contain at least one number"
            )

        return value