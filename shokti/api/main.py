"""FastAPI app — CORS, startup, all routers."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import text

from shokti.api.config import CORS_ORIGINS
from shokti.api.db import engine, Base, LIVENESS_TABLES
from shokti.api.routers.auth import router as auth_router
from shokti.api.routers.catalog import router as catalog_router
from shokti.api.routers.mcqs import router as mcqs_router
from shokti.api.routers.exams import router as exams_router
from shokti.api.routers.practice import router as practice_router
from shokti.api.routers.generate import router as generate_router
from shokti.api.routers.student import router as student_router



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create SQLAlchemy-modeled tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Create raw-SQL tables (student_answer_log, student_mcq_stats)
        for stmt in LIVENESS_TABLES.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                await conn.execute(text(stmt))
    yield
    await engine.dispose()


app = FastAPI(
    title="Shokti MCQ API",
    description="Adaptive MCQ practice API for Bangladeshi medical admission",
    version="1.0.0",
    lifespan=lifespan,
)

origins = CORS_ORIGINS or ["http://localhost:3000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(catalog_router)
app.include_router(mcqs_router)
app.include_router(exams_router)
app.include_router(practice_router)
app.include_router(generate_router)
app.include_router(student_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def read_index():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")