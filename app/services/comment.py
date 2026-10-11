import base64
from datetime import datetime
from typing import Dict, Optional, Tuple

from fastapi import HTTPException, status

from app.core import events
from app.core.database import transaction
from app.core.time import as_utc, utc_now
from app.models.auth.user import User
from app.models.comment import Comment
from app.models.project import Project
from app.models.task import Task
from app.models.workspace_member import MemberRole
from app.realtime.protocol import iso_z
from app.repositories import comments as comments_repository
from app.repositories import projects as projects_repository
from app.repositories import tasks as tasks_repository
from app.repositories import users as users_repository
from app.schemas.comment import CommentBody, CommentListResponse, CommentResponse
from app.schemas.workspace_member import public_profile
from app.services.activity import record_activity
from app.services.workspace_access import WorkspaceAccess, require_workspace_member


_EXCERPT_LENGTH = 140


def _excerpt(content: str) -> str:
    compact = " ".join(content.split())
    if len(compact) <= _EXCERPT_LENGTH:
        return compact
    return compact[: _EXCERPT_LENGTH - 3] + "..."


def _deny_viewer(access: WorkspaceAccess, detail: str) -> None:
    if access.membership.role == MemberRole.VIEWER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


async def _access_for_task(task_id: str, current_user: User) -> Tuple[Task, Project, WorkspaceAccess]:
    task = await tasks_repository.find_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    project = await projects_repository.find_active_by_id(task.project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    access = await require_workspace_member(project.workspace_id, current_user)
    return task, project, access


async def _response(comment: Comment, user: Optional[User] = None) -> CommentResponse:
    if user is None:
        user = await users_repository.find_by_id(comment.author_id)
    profile = public_profile(user, comment.author_id)
    return CommentResponse(
        id=comment.id,
        task_id=comment.task_id,
        author_id=comment.author_id,
        author_name=profile["name"],
        avatar_url=profile["avatar_url"],
        content=comment.content,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
    )


def _event_data(comment: Comment, response: CommentResponse) -> Dict[str, str]:
    return {
        "id": comment.id,
        "comment_id": comment.id,
        "task_id": comment.task_id,
        "author_id": comment.author_id,
        "author_name": response.author_name,
        "avatar_url": response.avatar_url,
        "content": comment.content,
        "excerpt": _excerpt(comment.content),
        "created_at": iso_z(comment.created_at),
        "updated_at": iso_z(comment.updated_at),
    }


def _encode_cursor(comment: Comment) -> str:
    raw = "{0}|{1}".format(as_utc(comment.created_at).isoformat(), comment.id)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8").rstrip("=")


def _decode_cursor(cursor: str) -> Tuple[datetime, str]:
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
        timestamp, comment_id = raw.split("|", 1)
        if not comment_id:
            raise ValueError
        return as_utc(datetime.fromisoformat(timestamp)), comment_id
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid cursor",
        )


def _publish(event: Optional[Dict]) -> None:
    if event is not None:
        events.publish(event)


async def create_comment(task_id: str, current_user: User, payload: CommentBody) -> CommentResponse:
    task, project, access = await _access_for_task(task_id, current_user)
    _deny_viewer(access, "Viewers cannot comment on tasks")
    author_id = access.membership.user_id
    now = utc_now()
    author = await users_repository.find_by_id(author_id)
    async with transaction() as session:
        comment = await comments_repository.insert(
            {
                "workspace_id": project.workspace_id,
                "project_id": project.id,
                "task_id": task.id,
                "author_id": author_id,
                "content": payload.content,
                "created_at": now,
                "updated_at": now,
            },
            session=session,
        )
        response = await _response(comment, author)
        event = await record_activity(
            action="comment.created",
            workspace_id=project.workspace_id,
            project_id=project.id,
            task_id=task.id,
            actor_id=author_id,
            metadata={
                "comment_id": comment.id,
                "excerpt": _excerpt(comment.content),
            },
            session=session,
        )
    event["data"] = _event_data(comment, response)
    _publish(event)
    return response


async def list_comments(
    task_id: str,
    current_user: User,
    *,
    limit: int = 50,
    cursor: Optional[str] = None,
) -> CommentListResponse:
    task, _project, _access = await _access_for_task(task_id, current_user)
    comments = await comments_repository.list_for_task(task.id)
    if cursor:
        cursor_time, cursor_id = _decode_cursor(cursor)
        comments = [
            comment
            for comment in comments
            if (as_utc(comment.created_at), comment.id) > (cursor_time, cursor_id)
        ]
    page = comments[:limit]
    next_cursor = _encode_cursor(page[-1]) if len(comments) > limit and page else None
    users = await users_repository.find_by_ids([comment.author_id for comment in page])
    users_by_id = {user.id: user for user in users if user.id}
    items = []
    for comment in page:
        items.append(await _response(comment, users_by_id.get(comment.author_id)))
    return CommentListResponse(items=items, next_cursor=next_cursor)


async def _comment_access(comment_id: str, current_user: User) -> Tuple[Comment, Project, WorkspaceAccess]:
    comment = await comments_repository.find_by_id(comment_id)
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
    _task, project, access = await _access_for_task(comment.task_id, current_user)
    return comment, project, access


async def update_comment(
    comment_id: str,
    current_user: User,
    payload: CommentBody,
) -> CommentResponse:
    comment, project, access = await _comment_access(comment_id, current_user)
    _deny_viewer(access, "Viewers cannot edit comments")
    if comment.author_id != access.membership.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit your own comments",
        )
    now = utc_now()
    author = await users_repository.find_by_id(comment.author_id)
    async with transaction() as session:
        updated = await comments_repository.update_content(comment.id, payload.content, now, session=session)
        if updated is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
        response = await _response(updated, author)
        event = await record_activity(
            action="comment.updated",
            workspace_id=project.workspace_id,
            project_id=project.id,
            task_id=updated.task_id,
            actor_id=access.membership.user_id,
            metadata={
                "comment_id": updated.id,
                "excerpt": _excerpt(updated.content),
            },
            session=session,
        )
    event["data"] = _event_data(updated, response)
    _publish(event)
    return response


async def delete_comment(comment_id: str, current_user: User) -> None:
    comment, project, access = await _comment_access(comment_id, current_user)
    role = access.membership.role
    if role == MemberRole.VIEWER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Viewers cannot delete comments",
        )
    if comment.author_id != access.membership.user_id and role not in (MemberRole.OWNER, MemberRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own comments",
        )
    async with transaction() as session:
        deleted = await comments_repository.delete(comment.id, session=session)
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
        event = await record_activity(
            action="comment.deleted",
            workspace_id=project.workspace_id,
            project_id=project.id,
            task_id=comment.task_id,
            actor_id=access.membership.user_id,
            metadata={"comment_id": comment.id},
            session=session,
        )
    event["data"] = {
        "comment_id": comment.id,
        "task_id": comment.task_id,
    }
    _publish(event)
