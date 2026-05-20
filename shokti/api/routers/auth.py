"""Auth router: register, login, refresh, me."""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Response
from sqlalchemy.ext.asyncio import AsyncSession
from shokti.api.auth import (
    create_student,
    authenticate_student,
    create_access_token,
    create_refresh_token,
    store_refresh_token,
    validate_refresh_token,
)
from shokti.api.schemas import (
    RegisterRequest,
    LoginRequest,
    TokenResponse,
    RefreshRequest,
    StudentResponse,
)
from shokti.api.deps import get_db_dep, get_current_student_dep
from shokti.api.models import Student
from shokti.api.config import REFRESH_TOKEN_EXPIRE_DAYS

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db_dep)):
    student = await create_student(req.email, req.password, req.name, db)
    access_token = create_access_token(student.id)
    refresh_token = create_refresh_token(student.id)
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    await store_refresh_token(student.id, refresh_token, expires_at, db)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, response: Response, db: AsyncSession = Depends(get_db_dep)):
    student = await authenticate_student(req.email, req.password, db)
    access_token = create_access_token(student.id)
    refresh_token = create_refresh_token(student.id)
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    await store_refresh_token(student.id, refresh_token, expires_at, db)
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86400,
    )
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(req: RefreshRequest, db: AsyncSession = Depends(get_db_dep)):
    student = await validate_refresh_token(req.refresh_token, db)
    if not student:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")
    access_token = create_access_token(student.id)
    new_refresh_token = create_refresh_token(student.id)
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    await store_refresh_token(student.id, new_refresh_token, expires_at, db)
    return TokenResponse(access_token=access_token, refresh_token=new_refresh_token)


@router.get("/me", response_model=StudentResponse)
async def me(student: Student = Depends(get_current_student_dep)):
    return StudentResponse.model_validate(student)