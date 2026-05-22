from pydantic import BaseModel, EmailStr, Field
from datetime import datetime, timedelta
from jose import jwt,JWTError
from fastapi import Depends, HTTPException, logger, status, Request, WebSocket
from fastapi.security import OAuth2PasswordBearer
import logging

logger = logging.getLogger(__name__)
SECRET_KEY = "super-secret-key"
ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    
class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    
def create_access_token(data: dict, expires_minutes: int = 15):
    to_encode = data.copy()
    to_encode.update({
        "type": "access"
    })
    expire = datetime.utcnow() + timedelta(minutes=expires_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(data: dict, expires_days: int = 7):
    to_encode = data.copy()
    to_encode.update({
        "type": "refresh"
    })
    expire = datetime.utcnow() + timedelta(days=expires_days)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
    
async def get_current_user(request: Request):
    token = request.cookies.get("access_token")
    
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")

        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")

        return user_id

    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
        
async def get_current_user_ws(websocket: WebSocket) -> str | None:
    """
    WebSocket-compatible version of get_current_user.
    Reads access_token from cookies in the WS handshake.
    Returns user_id on success, or closes the socket and returns None on failure.
    """
    logger.info("WS cookies: %s", dict(websocket.cookies))
    logger.info("WS headers: %s", dict(websocket.headers))
    token = websocket.cookies.get("access_token")
    logger.info("Token found: %s", bool(token))
    if not token:
        await websocket.close(code=4001)  # 4001 = unauthorized
        return None

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            await websocket.close(code=4001)
            return None

        user_id = payload.get("sub")
        if not user_id:
            await websocket.close(code=4001)
            return None

        return user_id
    except JWTError:
        await websocket.close(code=4001)
        return None
