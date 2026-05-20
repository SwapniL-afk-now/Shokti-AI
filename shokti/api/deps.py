"""FastAPI dependencies — get_db, get_current_student."""
from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from shokti.api.db import get_db
from shokti.api.auth import get_current_student
from shokti.api.models import Student

security = HTTPBearer()


async def get_current_student_dep(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Student:
    token = credentials.credentials
    return await get_current_student(token, db)


async def get_db_dep() -> AsyncSession:
    async for session in get_db():
        yield session