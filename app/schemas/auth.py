from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.security.email import is_valid_auth_email, normalize_email


class AuthPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class TurnstileRequest(AuthPayload):
    turnstileToken: str | None = Field(default=None, max_length=2048)


class EmailRequest(TurnstileRequest):
    email: str = Field(max_length=254)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        value = normalize_email(value)
        if not is_valid_auth_email(value):
            raise ValueError("Enter a valid email address")
        return value


class EmailCredentials(EmailRequest):
    password: str = Field(min_length=1, max_length=128)


class SignupRequest(EmailCredentials):
    learningTrackId: str | None = Field(default=None, min_length=1, max_length=64)


class TokenRequest(AuthPayload):
    token: str = Field(min_length=20, max_length=128)


class ResetPasswordRequest(TokenRequest):
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(AuthPayload):
    currentPassword: str = Field(min_length=1, max_length=128)
    newPassword: str = Field(min_length=1, max_length=128)


class SetPasswordRequest(AuthPayload):
    password: str = Field(min_length=1, max_length=128)


class ProfileRequest(AuthPayload):
    name: str = Field(min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value):
        if not value.strip():
            raise ValueError("name is required")
        return value.strip()
