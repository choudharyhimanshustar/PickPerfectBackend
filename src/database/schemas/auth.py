from pydantic import BaseModel, EmailStr, Field

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    
class LoginRequest(BaseModel):
    email: EmailStr
    password: str