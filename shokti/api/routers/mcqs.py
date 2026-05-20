"""MCQ router: list and detail with filters."""
import json
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from shokti.api.deps import get_db_dep
from shokti.api.schemas import MCQListItem, MCQDetailResponse, MCQOptions, MCQCorrectAnswer

router = APIRouter(prefix="/api/mcqs", tags=["mcqs"])


@router.get("", response_model=list[MCQListItem])
async def list_mcqs(
    topic_ids: str | None = Query(None, description="comma-separated topic_ids"),
    chapter_ids: str | None = Query(None, description="comma-separated chapter_ids"),
    subject: str | None = None,
    difficulty: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_dep),
):
    from sqlalchemy import text
    conditions = []
    params: dict = {}

    if topic_ids:
        tids = [t.strip() for t in topic_ids.split(",")]
        placeholders = ",".join(f":tid{i}" for i in range(len(tids)))
        conditions.append(f"topic_id IN ({placeholders})")
        for i, tid in enumerate(tids):
            params[f"tid{i}"] = tid
    if chapter_ids:
        cids = [c.strip() for c in chapter_ids.split(",")]
        placeholders = ",".join(f":cid{i}" for i in range(len(cids)))
        conditions.append(f"chapter_id IN ({placeholders})")
        for i, cid in enumerate(cids):
            params[f"cid{i}"] = cid
    if subject:
        conditions.append("subject = :subject")
        params["subject"] = subject
    if difficulty:
        conditions.append("difficulty = :difficulty")
        params["difficulty"] = difficulty

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    params["limit"] = limit
    params["offset"] = offset

    query = f"""
        SELECT id, chapter_id, chapter_name, topic_id, topic_name,
               difficulty, book_page_range
        FROM question_bank
        {where_clause}
        ORDER BY id
        LIMIT :limit OFFSET :offset
    """
    result = await db.execute(text(query), params)
    rows = result.fetchall()
    return [MCQListItem.model_validate(dict(r._mapping)) for r in rows]


@router.get("/{mcq_id}", response_model=MCQDetailResponse)
async def get_mcq(mcq_id: int, db: AsyncSession = Depends(get_db_dep)):
    from sqlalchemy import text
    result = await db.execute(text("SELECT * FROM question_bank WHERE id = :id"), {"id": mcq_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCQ not found")

    r = dict(row._mapping)

    try:
        options = json.loads(r["options"]) if isinstance(r["options"], str) else r["options"]
    except (json.JSONDecodeError, TypeError):
        options = {"A": "", "B": "", "C": "", "D": ""}

    try:
        correct = json.loads(r["correct_answer"]) if isinstance(r["correct_answer"], str) else r["correct_answer"]
    except (json.JSONDecodeError, TypeError):
        correct = {"option": "A", "text": ""}

    try:
        practice_related = json.loads(r["practice_related_questions"]) if isinstance(r["practice_related_questions"], str) else r["practice_related_questions"]
    except (json.JSONDecodeError, TypeError):
        practice_related = []

    return MCQDetailResponse(
        id=r["id"],
        chapter_id=r["chapter_id"] or "",
        chapter_name=r["chapter_name"] or "",
        topic_id=r["topic_id"] or "",
        topic_name=r["topic_name"] or "",
        difficulty=r["difficulty"],
        book_page_range=r["book_page_range"] or "",
        question=r["question"] or "",
        options=MCQOptions(**options),
        correct_answer=MCQCorrectAnswer(**correct),
        source_quote=r.get("source_quote"),
        pdf_page_number=r.get("pdf_page_number"),
        explanation=None,
    )