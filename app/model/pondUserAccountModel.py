from pydantic import BaseModel


class UserProfileUpdatePayload(BaseModel):
    name: str | None = None
    location: str | None = None


class PondDetailsUpdatePayload(BaseModel):
    name: str
    location: str | None = None
    status: str | None = None  # "Active" | "Inactive"


class ChangePasswordPayload(BaseModel):
    current_password: str
    new_password: str
