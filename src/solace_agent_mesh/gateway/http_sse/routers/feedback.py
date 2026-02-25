"""
API Router for receiving and processing user feedback on chat messages.
"""

import logging
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi import Request as FastAPIRequest
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from ..dependencies import (
    get_db,
    get_feedback_service,
    get_task_repository,
    get_user_config,
    get_user_id,
)
from ..repository.entities import Feedback
from ..repository.feedback_repository import FeedbackRepository
from ..repository.interfaces import ITaskRepository
from ..services.feedback_service import FeedbackService
from solace_agent_mesh.shared.api.pagination import PaginationParams
from solace_agent_mesh.shared.utils.types import UserId

router = APIRouter()
log = logging.getLogger(__name__)


class FeedbackPayload(BaseModel):
    """Data model for the feedback submission payload."""

    task_id: str = Field(..., alias="taskId")
    session_id: str = Field(..., alias="sessionId")
    feedback_type: Literal["up", "down"] = Field(..., alias="feedbackType")
    feedback_text: Optional[str] = Field(None, alias="feedbackText")


@router.get("/feedback", response_model=list[Feedback], tags=["Feedback"])
async def get_feedback(
    request: FastAPIRequest,
    start_date: str | None = None,
    end_date: str | None = None,
    task_id: str | None = None,
    session_id: str | None = None,
    rating: Literal["up", "down"] | None = None,
    page: int = 1,
    page_size: int = 20,
    query_user_id: str | None = None,
    db: DBSession = Depends(get_db),
    user_id: UserId = Depends(get_user_id),
    user_config: dict = Depends(get_user_config),
    task_repo: ITaskRepository = Depends(get_task_repository),
):
    """
    Retrieves feedback with flexible filtering and security controls.

    Regular users can only view their own feedback.
    Users with the 'feedback:read:all' scope can view any user's feedback.

    Query Parameters:
    - start_date: Filter feedback created after this date (ISO 8601 format)
    - end_date: Filter feedback created before this date (ISO 8601 format)
    - task_id: Filter by specific task ID
    - session_id: Filter by specific session ID
    - rating: Filter by rating type ("up" or "down")
    - page: Page number (default: 1)
    - page_size: Results per page (default: 20)
    - query_user_id: (Admin only) Query feedback for a specific user
    """
    log_prefix = "[GET /api/v1/feedback] "
    log.info("%sRequest from user %s", log_prefix, user_id)

    # Determine target user and permissions
    target_user_id = user_id
    can_query_all = user_config.get("scopes", {}).get("feedback:read:all", False)

    if query_user_id:
        if can_query_all:
            target_user_id = query_user_id
            log.info(
                "%sAdmin user %s is querying feedback for user %s",
                log_prefix,
                user_id,
                target_user_id,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to query other users' feedback.",
            )
    elif can_query_all:
        target_user_id = "*"
        log.info("%sAdmin user %s is querying feedback for all users.", log_prefix, user_id)

    # Verify task ownership if task_id filter is provided
    if task_id:
        task = task_repo.find_by_id(db, task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task with ID '{task_id}' not found.",
            )
        if task.user_id != user_id and not can_query_all:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view feedback for this task.",
            )

    # Parse date filters
    start_time_ms = None
    if start_date:
        try:
            start_time_ms = int(datetime.fromisoformat(start_date).timestamp() * 1000)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid start_date format. Use ISO 8601 format.",
            )

    end_time_ms = None
    if end_date:
        try:
            end_time_ms = int(datetime.fromisoformat(end_date).timestamp() * 1000)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid end_date format. Use ISO 8601 format.",
            )

    pagination = PaginationParams(page_number=page, page_size=page_size)

    try:
        repo = FeedbackRepository()
        feedback_list = repo.search(
            db,
            user_id=target_user_id,
            start_date=start_time_ms,
            end_date=end_time_ms,
            task_id=task_id,
            session_id=session_id,
            rating=rating,
            pagination=pagination,
        )
        log.info("%sReturning %d feedback entries", log_prefix, len(feedback_list))
        return feedback_list
    except Exception as e:
        log.exception("%sError searching for feedback: %s", log_prefix, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while searching for feedback.",
        )


@router.get("/feedback/summary", tags=["Feedback"])
async def get_feedback_summary(
    db: DBSession = Depends(get_db),
    user_id: UserId = Depends(get_user_id),
    user_config: dict = Depends(get_user_config),
):
    """
    Returns aggregate feedback statistics for the current user
    (or all users if the caller has feedback:read:all scope).
    """
    from ..repository.models.feedback_model import FeedbackModel
    from sqlalchemy import case
    from sqlalchemy import func as sqlfunc

    can_query_all = user_config.get("scopes", {}).get("feedback:read:all", False)

    query = db.query(
        sqlfunc.count(FeedbackModel.id).label("total"),
        sqlfunc.sum(case((FeedbackModel.rating == "up", 1), else_=0)).label("upvotes"),
        sqlfunc.sum(case((FeedbackModel.rating == "down", 1), else_=0)).label("downvotes"),
    )

    if not can_query_all:
        query = query.filter(FeedbackModel.user_id == user_id)

    row = query.first()
    total = row.total or 0
    upvotes = int(row.upvotes or 0)
    downvotes = int(row.downvotes or 0)

    return {
        "total": total,
        "upvotes": upvotes,
        "downvotes": downvotes,
        "sentimentScore": round(upvotes / total, 2) if total > 0 else None,
    }


@router.post("/feedback", status_code=202, tags=["Feedback"])
async def submit_feedback(
    payload: FeedbackPayload,
    request: FastAPIRequest,
    user_id: str = Depends(get_user_id),
    feedback_service: FeedbackService = Depends(get_feedback_service),
):
    """
    Receives and processes user feedback for a specific task.
    """
    await feedback_service.process_feedback(payload, user_id)
    return {"status": "feedback received"}
