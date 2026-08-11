from fastapi import APIRouter, HTTPException, status
from app.models.user import UserRegister, UserLogin, TokenResponse, UserResponse
from app.services.auth_service import hash_password, verify_password, create_access_token
from app.services.db_service import db_service
from app.config import logger

router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(user_data: UserRegister):
    """
    Register a new user with a hashed password and role.
    Note: Currently allows self-assigning any role ('admin' or 'employee').
    """
    logger.info("Registration attempt for username: %s", user_data.username)
    
    # Check if username already exists
    existing_user = db_service.get_user_by_username(user_data.username)
    if existing_user:
        logger.warning("Registration failed: Username '%s' already exists", user_data.username)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered."
        )
    
    # Hash password and create record
    hashed_pwd = hash_password(user_data.password)
    user_id = db_service.create_user(
        username=user_data.username,
        hashed_password=hashed_pwd,
        role=user_data.role
    )
    
    logger.info("Successfully registered user '%s' (ID: %d) with role: %s", user_data.username, user_id, user_data.role)
    
    # Retrieve the newly created user to return it
    new_user = db_service.get_user_by_username(user_data.username)
    return UserResponse(
        id=new_user["id"],
        username=new_user["username"],
        role=new_user["role"],
        created_at=new_user["created_at"]
    )

@router.post("/login", response_model=TokenResponse)
def login(login_data: UserLogin):
    """Log in and retrieve a JWT Bearer access token."""
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
    
    logger.info("User '%s' logged in successfully. Access token issued.", login_data.username)
    return TokenResponse(
        access_token=access_token,
        token_type="bearer"
    )
