"""JWT utils, password hashing, get_current_user."""
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, text

from shokti.api.config import JWT_SECRET, JWT_ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS
from shokti.api.models import Student, RefreshToken

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# Pre-computed dummy hash — computed once so timing is consistent across calls
_DUMMY_HASH = pwd_context.hash("dummy-placeholder")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(student_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": student_id, "exp": expire, "type": "access", "aud": "shokti-api"}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(student_id: str) -> str:
    import uuid
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {"sub": student_id, "exp": expire, "type": "refresh", "aud": "shokti-api", "jti": str(uuid.uuid4())}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM], audience="shokti-api")
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def get_current_student(token: str, db: AsyncSession) -> Student:
    """Look up student by ID from an already-open async session."""
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    student_id = payload["sub"]
    result = await db.execute(select(Student).where(Student.id == student_id))
    student = result.scalar_one_or_none()
    if student is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Student not found")
    return student


async def create_student(email: str, password: str, name: str | None, db: AsyncSession) -> Student:
    """Create a new student within an existing session (no new session opened)."""
    result = await db.execute(select(Student).where(Student.email == email.lower()))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    student = Student(email=email.lower(), password_hash=hash_password(password), name=name)
    db.add(student)
    await db.commit()
    await db.refresh(student)
    return student


async def authenticate_student(email: str, password: str, db: AsyncSession) -> Student:
    """Verify credentials within an existing session."""
    result = await db.execute(select(Student).where(Student.email == email.lower()))
    student = result.scalar_one_or_none()
    # Always run verify_password to prevent timing attack enumeration.
    # Use pre-computed _DUMMY_HASH so timing is constant regardless of student existence.
    if not verify_password(password, student.password_hash if student else _DUMMY_HASH):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if student is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return student


async def store_refresh_token(student_id: str, token: str, expires_at: datetime, db: AsyncSession) -> None:
    """Store a refresh token hash within an existing session. Replaces existing token of same hash."""
    token_hash = hash_token(token)
    # Delete any existing token with this hash to prevent duplicates
    result = await db.execute(
        select(RefreshToken).where(
            and_(
                RefreshToken.student_id == student_id,
                RefreshToken.token_hash == token_hash,
            )
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        await db.delete(existing)
    rt = RefreshToken(student_id=student_id, token_hash=token_hash, expires_at=expires_at)
    db.add(rt)
    await db.commit()


async def validate_refresh_token(token: str, db: AsyncSession) -> Student | None:
    """Validate a refresh token within an existing session. Invalidates token after use."""
    payload = decode_token(token)
    if payload.get("type") != "refresh":
        return None
    student_id = payload["sub"]
    token_hash = hash_token(token)
    # Atomic delete - only deletes if token exists AND is not expired
    # Using a single DELETE...RETURNING-like pattern (SQLite doesn't support RETURNING, so we check affected rows)
    result = await db.execute(
        text("""
            DELETE FROM refresh_tokens
            WHERE student_id = :sid
              AND token_hash = :hash
              AND expires_at > :now
        """),
        {"sid": student_id, "hash": token_hash, "now": datetime.now(timezone.utc)}
    )
    await db.commit()
    # If no rows deleted, token was already used/invalid
    if result.rowcount == 0:
        return None
    result = await db.execute(select(Student).where(Student.id == student_id))
    return result.scalar_one_or_none()