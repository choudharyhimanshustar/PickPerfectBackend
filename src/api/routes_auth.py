# routes/auth.py
from fastapi import APIRouter, HTTPException, status, Response, Request
from fastapi.responses import JSONResponse
from src.database.schemas.auth import SignupRequest, LoginRequest
from src.database.collections import get_users_collection
from src.core.security import hash_password,verify_password
from datetime import datetime
import logging
from src.database.schemas.auth import create_access_token, create_refresh_token, decode_access_token

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest):
    # 1. Check if user exists
    users_collection = get_users_collection()
    existing_user = await users_collection.find_one(
        {"email": payload.email}
    )
    logger.info("Checking if user exists",payload.email)
    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="User already exists",
        )

    # 2. Hash password
    logger.info("Hashing password for new user",payload.password)
    hashed_password = hash_password(payload.password)

    # 3. Insert user
    await users_collection.insert_one({
        "email": payload.email,
        "password": hashed_password,
        "created_at": datetime.utcnow(),
    })

    return {"success": True}

@router.post("/login")
async def login(payload: LoginRequest):
    users_collection = get_users_collection()
    user = await users_collection.find_one({"email": payload.email})

    if not user or not verify_password(payload.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    access_token = create_access_token({"sub": str(user["_id"])})
    refresh_token = create_refresh_token({"sub": str(user["_id"])})

    res = JSONResponse(content={"success": True})

    res.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 15,
        path="/",
    )

    res.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/",
    )
    logger.info("User logged in successfully",res)
    return res

@router.get("/me")
async def me(request: Request):
    access_token = request.cookies.get("access_token")

    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    payload = decode_access_token(access_token)

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    return {
        "authenticated": True,
        "user_id": payload.get("sub"),
        "email": payload.get("email"),
    }
    
@router.post("/refresh")
async def refresh(request: Request):
    refresh_token = request.cookies.get("refresh_token")

    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No refresh token",
        )

    payload = decode_access_token(refresh_token)

    if not payload or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    user_id = payload.get("sub")
    new_access_token = create_access_token({"sub": user_id})

    res = JSONResponse(content={"success": True})
    res.set_cookie(
        key="access_token",
        value=new_access_token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 15,
        path="/",
    )
    return res

@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(
        key="access_token",
        path="/",
    )
    response.delete_cookie(
        key="refresh_token",
        path="/",
    )

    logger.info("User logged out successfully")

    return {"success": True}
