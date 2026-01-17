# routes/auth.py
from fastapi import APIRouter, HTTPException, status
from src.database.schemas.auth import SignupRequest, LoginRequest
from src.database.collections import get_users_collection
from src.core.security import hash_password,verify_password
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest):
    # 1. Check if user exists
    users_collection = get_users_collection()
    existing_user = await users_collection.find_one(
        {"email": payload.email}
    )
    print("Checking if user exists",payload.email)
    logger.info("Checking if user exists",payload.email)
    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="User already exists",
        )

    # 2. Hash password
    print("Hashing password for new user",payload.password)
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

    # ❌ User not found
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )

    # ❌ Password mismatch
    if not verify_password(payload.password, user["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )

    # ✅ Success (JWT will be added here later)
    return {
        "success": True,
        "message": "Login successful",
        "user": {
            "email": user["email"],
        }
    }
