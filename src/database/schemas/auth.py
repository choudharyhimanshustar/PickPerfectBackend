from pydantic import BaseModel, EmailStr, Field
from datetime import datetime, timedelta
from jose import jwt,JWTError

SECRET_KEY = "super-secret-key"
ALGORITHM = "HS256"
class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    
class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    
def create_access_token(data: dict, expires_minutes: int = 15):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=expires_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(data: dict, expires_days: int = 7):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=expires_days)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None