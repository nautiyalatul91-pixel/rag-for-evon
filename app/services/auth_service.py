import jwt
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext

from app.config import JWT_SECRET_KEY, JWT_ALGORITHM, logger
from app.services.db_service import db_service

# Password hashing configuration
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Bearer Token extraction scheme
bearer_scheme = HTTPBearer(auto_error=False)

def hash_password(password: str) -> str:
    """Returns the bcrypt hash of a plaintext password."""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plaintext password against a bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Generates a JWT access token containing user metadata and expiry timestamp."""
    to_encode = data.copy()
    now_ts = time.time()
    if expires_delta:
        expire_ts = now_ts + expires_delta.total_seconds()
    else:
        expire_ts = now_ts + (24 * 3600)  # Default 24 hours
    
    to_encode.update({"exp": int(expire_ts)})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str) -> Optional[dict]:
    """Decodes a JWT access token and returns the payload if valid."""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        # Validate structure
        if "username" not in payload or "role" not in payload:
            return None
        return payload
    except jwt.PyJWTError as e:
        logger.warning("JWT Token validation failed: %s", e)
        return None

async def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)) -> dict:
    """
    FastAPI dependency to retrieve the currently logged-in user from the JWT Bearer token.
    Raises 401 Unauthorized if the token is missing, expired, or invalid.
    """
    if not credentials or not credentials.credentials:
        from app.config import audit_logger
        audit_logger.info("User: Unknown | Role: Unknown | Endpoint: Protected | Success: False | Details: Missing Bearer Token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided."
        )
    
    # Extract and sanitize the token
    token = credentials.credentials.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    token = token.strip('"').strip("'")
    
    payload = decode_access_token(token)
    if not payload:
        from app.config import audit_logger
        audit_logger.info("User: Unknown | Role: Unknown | Endpoint: Protected | Success: False | Details: Invalid or expired token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials. Invalid or expired token."
        )
    
    username = payload.get("username")
    user = db_service.get_user_by_username(username)
    if not user:
        from app.config import audit_logger
        audit_logger.info("User: Unknown | Role: Unknown | Endpoint: Protected | Success: False | Details: Username '%s' not found", username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User associated with this token does not exist."
        )
    
    # Return user data dict (excluding sensitive data)
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "created_at": user["created_at"]
    }

def require_role(allowed_roles: List[str]):
    """FastAPI dependency factory to enforce role-based access control (RBAC)."""
    def role_checker(current_user: dict = Depends(get_current_user)) -> dict:
        if current_user["role"] not in allowed_roles:
            from app.config import audit_logger
            audit_logger.info(
                "User: %s | Role: %s | Endpoint: Protected | Success: False | Details: Permission denied. Required: %s",
                current_user["username"], current_user["role"], str(allowed_roles)
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required role: one of {allowed_roles}."
            )
        return current_user
    return role_checker

# Reusable role dependencies
require_admin = require_role(["admin"])
require_employee_or_admin = require_role(["admin", "employee"])
