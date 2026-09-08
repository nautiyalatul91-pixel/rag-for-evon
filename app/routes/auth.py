from typing import List
from fastapi import APIRouter, HTTPException, status, Depends
from app.models.user import UserRegister, UserLogin, TokenResponse, UserResponse, AvailableRoleResponse, UserMeResponse
from app.services.auth_service import hash_password, verify_password, create_access_token, get_current_user
from app.services.db_service import db_service
from app.config import logger, audit_logger

router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.get("/available-roles", response_model=List[AvailableRoleResponse])
def get_available_roles():
    """
    Public endpoint returning active roles for the registration dropdown.
    """
    try:
        roles = db_service.list_roles()
        return [AvailableRoleResponse(role_id=r["role_id"], role_name=r["role_name"]) for r in roles]
    except Exception as e:
        logger.error("Failed to fetch available roles: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve available roles."
        )

@router.get("/me", response_model=UserMeResponse)
def get_me(current_user: dict = Depends(get_current_user)):
    """
    Retrieve currently authenticated user details with dynamic permissions.
    """
    role_id = current_user.get("role", "")
    role_info = db_service.get_role(role_id)
    can_upload = bool(role_info["can_upload"]) if role_info else False
    is_admin = (role_id == "admin" or (role_info and bool(role_info.get("is_protected", False))))
    return UserMeResponse(
        id=current_user["id"],
        username=current_user["username"],
        role=role_id,
        can_upload=can_upload,
        is_admin=is_admin,
        created_at=current_user["created_at"]
    )

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(user_data: UserRegister):
    """
    Register a new user with a hashed password and validated role from the roles table.
    """
    logger.info("Registration attempt for username: %s with requested role: %s", user_data.username, user_data.role)
    
    # 1. Validate role against dynamic roles table
    roles = db_service.list_roles()
    requested_role = user_data.role.strip()
    
    matched_role = None
    for r in roles:
        if requested_role.lower() in (r["role_id"].lower(), r["role_name"].lower()):
            matched_role = r
            break
            
    if not matched_role:
        logger.warning(
            "Registration rejected: Invalid role '%s' for username '%s'.",
            requested_role, user_data.username
        )
        audit_logger.info(
            "User: anonymous | Role: none | Endpoint: POST /auth/register | Success: False | Details: Invalid role '%s' for username '%s'",
            requested_role, user_data.username
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role '{requested_role}'. Please choose from available roles: {[r['role_id'] for r in roles]}"
        )
    
    # 2. Check if username already exists
    existing_user = db_service.get_user_by_username(user_data.username)
    if existing_user:
        logger.warning("Registration failed: Username '%s' already exists", user_data.username)
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /auth/register | Success: False | Details: Username already exists",
            user_data.username, matched_role["role_id"]
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered."
        )
    
    # 3. Hash password and create record
    hashed_pwd = hash_password(user_data.password)
    user_id = db_service.create_user(
        username=user_data.username,
        hashed_password=hashed_pwd,
        role=matched_role["role_id"]
    )
    
    logger.info("Successfully registered user '%s' (ID: %d) with role: %s", user_data.username, user_id, matched_role["role_id"])
    audit_logger.info(
        "User: %s | Role: %s | Endpoint: POST /auth/register | Success: True | Details: User registered successfully",
        user_data.username, matched_role["role_id"]
    )
    
    # 4. Retrieve the newly created user to return it
    new_user = db_service.get_user_by_username(user_data.username)
    return UserResponse(
        id=new_user["id"],
        username=new_user["username"],
        role=new_user["role"],
        created_at=new_user["created_at"]
    )

@router.post("/login", response_model=TokenResponse)
def login(login_data: UserLogin):
    """Log in and retrieve a JWT Bearer access token with dynamic permission metadata."""
    logger.info("Login attempt for username: %s", login_data.username)
    
    user = db_service.get_user_by_username(login_data.username)
    if not user or not verify_password(login_data.password, user["hashed_password"]):
        logger.warning("Login failed: Invalid credentials for username '%s'", login_data.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password."
        )
    
    # Create token containing user info (username and role)
    token_payload = {
        "username": user["username"],
        "role": user["role"]
    }
    access_token = create_access_token(data=token_payload)
    
    role_info = db_service.get_role(user["role"])
    can_upload = bool(role_info["can_upload"]) if role_info else False
    is_admin = (user["role"] == "admin" or (role_info and bool(role_info.get("is_protected", False))))
    
    logger.info("User '%s' logged in successfully. Role: '%s' (can_upload: %s).", login_data.username, user["role"], can_upload)
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        role=user["role"],
        can_upload=can_upload,
        is_admin=is_admin
    )
