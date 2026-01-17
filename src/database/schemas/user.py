# schemas/user.py
from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Optional

class UserBase(BaseModel):
    email: EmailStr

class UserCreate(UserBase):
    password: str

class UserInDB(UserBase):
    id: Optional[str]
    password: str
    created_at: datetime
    updated_at: datetime

class UserResponse(UserBase):
    created_at: datetime
    

