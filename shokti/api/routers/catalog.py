"""Catalog router: subjects, books, chapters, topics."""
from fastapi import APIRouter, Depends, HTTPException, status, Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from shokti.api.deps import get_db_dep
from shokti.api.schemas import (
    SubjectResponse,
    BookResponse,
    ChapterBasicResponse,
    ChapterDetailResponse,
    TopicResponse,
)

router = APIRouter(prefix="/api", tags=["catalog"])


@router.get("/subjects", response_model=list[SubjectResponse])
async def list_subjects(db: AsyncSession = Depends(get_db_dep)):
    from shokti.api.models import Subject
    result = await db.execute(select(Subject).order_by(Subject.sort_order))
    subjects = result.scalars().all()
    return [SubjectResponse.model_validate(s) for s in subjects]


@router.get("/subjects/{subject_id}", response_model=SubjectResponse)
async def get_subject(subject_id: str, db: AsyncSession = Depends(get_db_dep)):
    from shokti.api.models import Subject
    result = await db.execute(select(Subject).where(Subject.id == subject_id))
    subject = result.scalar_one_or_none()
    if not subject:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subject not found")
    return SubjectResponse.model_validate(subject)


@router.get("/books", response_model=list[BookResponse])
async def list_books(subject_id: str | None = None, db: AsyncSession = Depends(get_db_dep)):
    from shokti.api.models import Book
    query = select(Book).order_by(Book.sort_order)
    if subject_id:
        query = query.where(Book.subject_id == subject_id)
    result = await db.execute(query)
    books = result.scalars().all()
    return [BookResponse.model_validate(b) for b in books]


@router.get("/books/{book_id}", response_model=BookResponse)
async def get_book(book_id: str, db: AsyncSession = Depends(get_db_dep)):
    from shokti.api.models import Book
    result = await db.execute(select(Book).where(Book.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    return BookResponse.model_validate(book)


@router.get("/chapters", response_model=list[ChapterBasicResponse])
async def list_chapters(
    book_id: str | None = None,
    subject_id: str | None = None,
    db: AsyncSession = Depends(get_db_dep),
):
    conditions = []
    params = {}
    if book_id:
        conditions.append("book_id = :book_id")
        params["book_id"] = book_id
    if subject_id:
        conditions.append("subject = :subject")
        params["subject"] = subject_id

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    result = await db.execute(
        text(f"""
            SELECT
                chapter_id,
                chapter_name,
                book_page_range,
                source_file,
                COUNT(*) as mcq_count
            FROM question_bank
            {where_clause}
            GROUP BY chapter_id, chapter_name
            ORDER BY chapter_id
        """),
        params,
    )
    rows = result.fetchall()
    return [
        ChapterBasicResponse(
            chapter_id=r.chapter_id or "",
            chapter_name=r.chapter_name or "",
            book_page_range=r.book_page_range or "",
            source_file=r.source_file or "",
            mcq_count=r.mcq_count,
        )
        for r in rows
    ]


@router.get("/chapters/{chapter_id}", response_model=ChapterDetailResponse)
async def get_chapter(chapter_id: str = Path(pattern=r"^\d+$"), db: AsyncSession = Depends(get_db_dep)):
    result = await db.execute(
        text("""
            SELECT
                MAX(chapter_name) as chapter_name,
                MAX(book_page_range) as book_page_range,
                MAX(source_file) as source_file
            FROM question_bank
            WHERE chapter_id = :chapter_id
        """),
        {"chapter_id": chapter_id},
    )
    row = result.fetchone()
    if not row or not row.chapter_name:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chapter not found")

    result = await db.execute(
        text("""
            SELECT topic_id, topic_name, book_page_range, COUNT(*) as mcq_count
            FROM question_bank
            WHERE chapter_id = :chapter_id
            GROUP BY topic_id, topic_name
            ORDER BY topic_name
        """),
        {"chapter_id": chapter_id},
    )
    topics = result.fetchall()
    return ChapterDetailResponse(
        chapter_id=chapter_id,
        chapter_name=row.chapter_name or "",
        book_page_range=row.book_page_range or "",
        source_file=row.source_file or "",
        mcq_count=sum(r.mcq_count for r in topics),
        topics=[
            TopicResponse(
                topic_id=r.topic_id or "",
                topic_name=r.topic_name or "",
                book_page_range=r.book_page_range or "",
                mcq_count=r.mcq_count,
                is_gap=r.mcq_count < 15,
            )
            for r in topics
        ],
    )


@router.get("/topics", response_model=list[TopicResponse])
async def list_topics(
    chapter_id: str | None = None,
    book_id: str | None = None,
    db: AsyncSession = Depends(get_db_dep),
):
    conditions = []
    params = {}
    if chapter_id:
        conditions.append("chapter_id = :chapter_id")
        params["chapter_id"] = chapter_id
    if book_id:
        conditions.append("book_id = :book_id")
        params["book_id"] = book_id

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    result = await db.execute(
        text(f"""
            SELECT topic_id, topic_name, book_page_range, COUNT(*) as mcq_count
            FROM question_bank
            {where_clause}
            GROUP BY topic_id, topic_name
            ORDER BY topic_name
        """),
        params,
    )
    rows = result.fetchall()
    return [
        TopicResponse(
            topic_id=r.topic_id or "",
            topic_name=r.topic_name or "",
            book_page_range=r.book_page_range or "",
            mcq_count=r.mcq_count,
            is_gap=r.mcq_count < 15,
        )
        for r in rows
    ]


@router.get("/topics/{topic_id}", response_model=TopicResponse)
async def get_topic(topic_id: str = Path(pattern=r"^\d+$"), db: AsyncSession = Depends(get_db_dep)):
    result = await db.execute(
        text("""
            SELECT topic_id, topic_name, book_page_range, COUNT(*) as mcq_count
            FROM question_bank
            WHERE topic_id = :topic_id
            GROUP BY topic_id, topic_name
        """),
        {"topic_id": topic_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found")
    return TopicResponse(
        topic_id=row.topic_id or "",
        topic_name=row.topic_name or "",
        book_page_range=row.book_page_range or "",
        mcq_count=row.mcq_count,
        is_gap=row.mcq_count < 15,
    )