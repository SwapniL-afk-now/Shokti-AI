"""API package — all routers exported here."""
from shokti.api.routers.auth import router as auth_router
from shokti.api.routers.catalog import router as catalog_router
from shokti.api.routers.mcqs import router as mcqs_router
from shokti.api.routers.exams import router as exams_router
from shokti.api.routers.practice import router as practice_router
from shokti.api.routers.generate import router as generate_router
from shokti.api.routers.student import router as student_router

__all__ = [
    "auth_router",
    "catalog_router",
    "mcqs_router",
    "exams_router",
    "practice_router",
    "generate_router",
    "student_router",
]