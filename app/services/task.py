import base64
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import HTTPException, status

from app.core import events
from app.core.database import transaction
from app.core.time import as_utc, utc_now
from app.realtime.protocol import build_envelope
from app.models.auth.user import User
from app.models.project import Project
from app.models.task import STATUS_RANK, ActivityAction, Task, TaskStatus
from app.models.workspace_member import MemberRole
from app.repositories import activities as activities_repository
from app.repositories import projects as projects_repository
from app.repositories import tasks as tasks_repository
from app.repositories import workspace_members as members_repository
from app.schemas.activity import ActivityListResponse, activity_item
from app.schemas.task import (
    TaskCreate,
    TaskListResponse,
    TaskMove,
    TaskReorder,
    TaskReorderResponse,
    TaskResponse,
    TaskUpdate,
    plain_value,
    task_response,
)
from app.services.workspace_access import WorkspaceAccess, require_workspace_member

_STALE_TASK = "Task has been modified. Refresh and try again."
_COLUMN_MISMATCH = "Every task must belong to this project and column"
_PARENT_PROJECT = "Parent task must belong to the same project"
_ASSIGNEE = "Assignee must be a member of this workspace"


def _deny_viewer(access: WorkspaceAccess, detail: str) -> None:
    if access.membership.role == MemberRole.VIEWER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _assert_can_delete(access: WorkspaceAccess, task: Task) -> None:
    role = access.membership.role
    if role == MemberRole.VIEWER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Viewers cannot delete tasks",
        )
    if role == MemberRole.MEMBER and task.created_by != access.membership.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete tasks you created",
        )


def _dates_conflict(start_date: Optional[datetime], due_date: Optional[datetime]) -> bool:
    if start_date is None or due_date is None:
        return False
    return as_utc(start_date) > as_utc(due_date)


def _normalize_changes(changes: Dict[str, Any]) -> Dict[str, Any]:
    normalized = {}
    for key, value in changes.items():
        normalized[key] = value.value if isinstance(value, Enum) else value
    return normalized


def _sort_key(task: Task) -> Tuple[int, int, str]:
    return (STATUS_RANK[task.status], task.position, task.id)


def _encode_cursor(task: Task) -> str:
    raw = "{0}:{1}:{2}".format(STATUS_RANK[task.status], task.position, task.id)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8").rstrip("=")


def _decode_cursor(cursor: str) -> Tuple[int, int, str]:
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
        rank_text, position_text, task_id = raw.split(":", 2)
        rank = int(rank_text)
        position = int(position_text)
        if rank not in STATUS_RANK.values() or position < 0 or not task_id:
            raise ValueError
        return (rank, position, task_id)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid cursor",
        )


def _ordered(tasks: Sequence[Task]) -> List[Task]:
    return sorted(tasks, key=lambda task: (task.position, task.id))


def _insert_at(tasks: Sequence[Task], moving: Task, position: int) -> List[Task]:
    placed = list(tasks)
    index = min(max(position, 0), len(placed))
    placed.insert(index, moving)
    return placed


def _completion(task: Task, new_status: str, now: datetime) -> Optional[datetime]:
    if new_status == TaskStatus.DONE.value:
        if task.status == TaskStatus.DONE and task.completed_at is not None:
            return task.completed_at
        return now
    return None


async def _project_or_404(project_id: str) -> Project:
    project = await projects_repository.find_active_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


async def _access_for_project(project_id: str, current_user: User) -> Tuple[Project, WorkspaceAccess]:
    project = await _project_or_404(project_id)
    access = await require_workspace_member(project.workspace_id, current_user)
    return project, access


async def _access_for_task(task_id: str, current_user: User) -> Tuple[Task, Project, WorkspaceAccess]:
    task = await tasks_repository.find_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    project = await projects_repository.find_active_by_id(task.project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    access = await require_workspace_member(project.workspace_id, current_user)
    return task, project, access


async def _assert_assignee(workspace_id: str, assignee_id: Optional[str]) -> None:
    if assignee_id is None:
        return
    member = await members_repository.find_by_workspace_and_user(workspace_id, assignee_id)
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_ASSIGNEE,
        )


async def _assert_parent(project_id: str, task_id: Optional[str], parent_id: Optional[str]) -> None:
    if parent_id is None:
        return
    if task_id is not None and parent_id == task_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A task cannot be its own parent",
        )
    seen = set()
    current = parent_id
    while current is not None:
        if current in seen or (task_id is not None and current == task_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Task parent would create a cycle",
            )
        seen.add(current)
        if len(seen) > 50:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Task parent would create a cycle",
            )
        parent = await tasks_repository.find_by_id(current)
        if parent is None or parent.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=_PARENT_PROJECT,
            )
        current = parent.parent_task_id


async def _record(
    *,
    workspace_id: str,
    project_id: str,
    task_id: Optional[str],
    actor_id: str,
    action: str,
    metadata: Dict[str, Any],
    session: Any,
) -> Dict[str, Any]:
    stored = {key: plain_value(value) for key, value in metadata.items()}
    occurred = utc_now()
    await activities_repository.insert(
        {
            "workspace_id": workspace_id,
            "project_id": project_id,
            "task_id": task_id,
            "actor_id": actor_id,
            "action": action,
            "metadata": stored,
            "created_at": occurred,
        },
        session=session,
    )
    return build_envelope(
        type=action,
        workspace_id=workspace_id,
        project_id=project_id,
        task_id=task_id,
        actor_id=actor_id,
        data=stored,
        occurred_at=occurred,
    )


def _publish(event: Optional[Dict[str, Any]]) -> None:
    if event is not None:
        events.publish(event)


async def _save(
    task_id: str,
    fields: Dict[str, Any],
    *,
    expected_version: Optional[int],
    session: Any,
) -> Task:
    updated = await tasks_repository.update(
        task_id,
        fields,
        expected_version=expected_version,
        session=session,
    )
    if updated is not None:
        return updated
    if expected_version is not None:
        current = await tasks_repository.find_by_id(task_id, session=session)
        if current is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_STALE_TASK)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")


async def _column(project_id: str, column_status: str, session: Any) -> List[Task]:
    return await tasks_repository.list_for_project(
        project_id,
        status=column_status,
        session=session,
    )


def _position_updates(column: Sequence[Task], skip_id: str) -> List[Tuple[Task, int]]:
    updates = []
    for index, task in enumerate(column):
        if task.id != skip_id and task.position != index:
            updates.append((task, index))
    return updates


async def _apply_positions(
    updates: Sequence[Tuple[Task, int]],
    session: Any,
) -> List[Task]:
    saved = []
    for task, position in updates:
        saved.append(
            await _save(
                task.id,
                {"position": position},
                expected_version=None,
                session=session,
            )
        )
    return saved


async def create_task(
    project_id: str,
    current_user: User,
    payload: TaskCreate,
) -> TaskResponse:
    project, access = await _access_for_project(project_id, current_user)
    _deny_viewer(access, "Viewers cannot create tasks")
    await _assert_assignee(project.workspace_id, payload.assignee_id)
    await _assert_parent(project.id, None, payload.parent_task_id)

    actor_id = access.membership.user_id
    now = utc_now()
    event = None
    async with transaction() as session:
        column = await _column(project.id, TaskStatus.TODO.value, session)
        task = await tasks_repository.insert(
            {
                "project_id": project.id,
                "title": payload.title,
                "description": payload.description,
                "status": TaskStatus.TODO.value,
                "priority": payload.priority.value,
                "assignee_id": payload.assignee_id,
                "created_by": actor_id,
                "start_date": payload.start_date,
                "due_date": payload.due_date,
                "position": len(column),
                "parent_task_id": payload.parent_task_id,
                "estimated_hours": payload.estimated_hours,
                "created_at": now,
                "updated_at": now,
                "completed_at": None,
                "version": 1,
            },
            session=session,
        )
        event = await _record(
            workspace_id=project.workspace_id,
            project_id=project.id,
            task_id=task.id,
            actor_id=actor_id,
            action=ActivityAction.CREATED.value,
            metadata={
                "title": task.title,
                "status": task.status.value,
                "priority": task.priority.value,
                "assignee_id": task.assignee_id,
                "position": task.position,
            },
            session=session,
        )
    _publish(event)
    return task_response(task)


async def list_tasks(
    project_id: str,
    current_user: User,
    *,
    task_status: Optional[str] = None,
    assignee_id: Optional[str] = None,
    priority: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[str] = None,
) -> TaskListResponse:
    project, _access = await _access_for_project(project_id, current_user)
    tasks = await tasks_repository.list_for_project(
        project.id,
        status=task_status,
        assignee_id=assignee_id,
        priority=priority,
    )
    if cursor is not None:
        marker = _decode_cursor(cursor)
        tasks = [task for task in tasks if _sort_key(task) > marker]
    page = tasks[:limit]
    next_cursor = _encode_cursor(page[-1]) if len(tasks) > limit and page else None
    return TaskListResponse(
        items=[task_response(task) for task in page],
        next_cursor=next_cursor,
    )


async def list_task_activity(task_id: str, current_user: User) -> ActivityListResponse:
    task, _project, _access = await _access_for_task(task_id, current_user)
    items = await activities_repository.list_for_task(task.id)
    return ActivityListResponse(items=[activity_item(item) for item in items])


async def get_task(task_id: str, current_user: User) -> TaskResponse:
    task, _project, _access = await _access_for_task(task_id, current_user)
    return task_response(task)


async def update_task(
    task_id: str,
    current_user: User,
    payload: TaskUpdate,
) -> TaskResponse:
    task, project, access = await _access_for_task(task_id, current_user)
    _deny_viewer(access, "Viewers cannot edit tasks")
    changes = _normalize_changes(payload.model_dump(exclude_unset=True))
    expected_version = changes.pop("version", None)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )
    if changes.get("status") == task.status.value:
        changes.pop("status")
        if not changes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update",
            )

    start_date = changes["start_date"] if "start_date" in changes else task.start_date
    due_date = changes["due_date"] if "due_date" in changes else task.due_date
    if _dates_conflict(start_date, due_date):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Start date cannot be after the due date",
        )
    if "assignee_id" in changes:
        await _assert_assignee(project.workspace_id, changes["assignee_id"])
    if "parent_task_id" in changes:
        await _assert_parent(project.id, task.id, changes["parent_task_id"])

    actor_id = access.membership.user_id
    event = None
    if "status" in changes:
        updated, event = await _change_status(
            task,
            project.workspace_id,
            actor_id,
            changes,
            expected_version,
        )
        _publish(event)
        return task_response(updated)

    action = ActivityAction.UPDATED.value
    metadata: Dict[str, Any] = {"fields": sorted(changes.keys())}
    if "assignee_id" in changes and changes["assignee_id"] != task.assignee_id:
        action = ActivityAction.ASSIGNED.value
        metadata = {
            "assignee_id": changes["assignee_id"],
            "previous_assignee_id": task.assignee_id,
            "fields": sorted(changes.keys()),
        }
    async with transaction() as session:
        updated = await _save(
            task.id,
            changes,
            expected_version=expected_version,
            session=session,
        )
        event = await _record(
            workspace_id=project.workspace_id,
            project_id=project.id,
            task_id=task.id,
            actor_id=actor_id,
            action=action,
            metadata=metadata,
            session=session,
        )
    _publish(event)
    return task_response(updated)


async def _change_status(
    task: Task,
    workspace_id: str,
    actor_id: str,
    changes: Dict[str, Any],
    expected_version: Optional[int],
) -> Tuple[Task, Dict[str, Any]]:
    new_status = changes["status"]
    extra = {key: value for key, value in changes.items() if key != "status"}
    now = utc_now()
    completed_at = _completion(task, new_status, now)
    event = None
    async with transaction() as session:
        target = await _column(task.project_id, new_status, session)
        source = await _column(task.project_id, task.status.value, session)
        placed = _insert_at(_ordered(target), task, len(target))
        moving_position = placed.index(next(item for item in placed if item.id == task.id))
        saved = await _save(
            task.id,
            dict(
                extra,
                status=new_status,
                position=moving_position,
                completed_at=completed_at,
            ),
            expected_version=expected_version,
            session=session,
        )
        await _apply_positions(_position_updates(_ordered(
            [item for item in source if item.id != task.id]
        ), task.id), session)
        event = await _record(
            workspace_id=workspace_id,
            project_id=task.project_id,
            task_id=task.id,
            actor_id=actor_id,
            action=ActivityAction.STATUS_CHANGED.value,
            metadata={
                "status": new_status,
                "previous_status": task.status.value,
                "completed_at": completed_at,
                "position": moving_position,
                "fields": sorted(changes.keys()),
            },
            session=session,
        )
    return saved, event


async def move_task(
    task_id: str,
    current_user: User,
    payload: TaskMove,
) -> TaskResponse:
    task, project, access = await _access_for_task(task_id, current_user)
    _deny_viewer(access, "Viewers cannot move tasks")
    if payload.version is not None and payload.version != task.version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_STALE_TASK)
    new_status = payload.status.value
    now = utc_now()
    completed_at = _completion(task, new_status, now)
    event = None
    saved = task
    async with transaction() as session:
        source = _ordered(await _column(task.project_id, task.status.value, session))
        if new_status == task.status.value:
            remaining = [item for item in source if item.id != task.id]
            placed = _insert_at(remaining, task, payload.position)
            source_updates: List[Tuple[Task, int]] = []
        else:
            target = _ordered(await _column(task.project_id, new_status, session))
            placed = _insert_at(target, task, payload.position)
            source_updates = _position_updates(
                [item for item in source if item.id != task.id],
                task.id,
            )
        moving_position = next(
            index for index, item in enumerate(placed) if item.id == task.id
        )
        unchanged = (
            new_status == task.status.value
            and moving_position == task.position
            and completed_at == task.completed_at
        )
        if unchanged and not any(item.position != index for index, item in enumerate(placed) if item.id != task.id):
            saved = task
        else:
            saved = await _save(
                task.id,
                {
                    "status": new_status,
                    "position": moving_position,
                    "completed_at": completed_at,
                },
                expected_version=payload.version,
                session=session,
            )
            await _apply_positions(_position_updates(placed, task.id), session)
            await _apply_positions(source_updates, session)
            event = await _record(
                workspace_id=project.workspace_id,
                project_id=project.id,
                task_id=task.id,
                actor_id=access.membership.user_id,
                action=ActivityAction.MOVED.value,
                metadata={
                    "status": new_status,
                    "previous_status": task.status.value,
                    "position": moving_position,
                    "previous_position": task.position,
                },
                session=session,
            )
    _publish(event)
    return task_response(saved)


async def reorder_tasks(
    project_id: str,
    current_user: User,
    payload: TaskReorder,
) -> TaskReorderResponse:
    project, access = await _access_for_project(project_id, current_user)
    _deny_viewer(access, "Viewers cannot reorder tasks")
    if len(payload.task_ids) != len(set(payload.task_ids)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Task ids must be unique",
        )

    column_status = payload.status.value
    event = None
    items: List[Task] = []
    async with transaction() as session:
        column = _ordered(await _column(project.id, column_status, session))
        current_ids = [task.id for task in column]
        if set(payload.task_ids) != set(current_ids):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_COLUMN_MISMATCH)
        if payload.task_ids == current_ids:
            items = column
        else:
            by_id = {task.id: task for task in column}
            ordered = [by_id[task_id] for task_id in payload.task_ids]
            saved = []
            for index, task in enumerate(ordered):
                if task.position == index:
                    saved.append(task)
                    continue
                saved.append(
                    await _save(
                        task.id,
                        {"position": index},
                        expected_version=None,
                        session=session,
                    )
                )
            items = saved
            event = await _record(
                workspace_id=project.workspace_id,
                project_id=project.id,
                task_id=items[0].id if items else None,
                actor_id=access.membership.user_id,
                action=ActivityAction.MOVED.value,
                metadata={
                    "status": column_status,
                    "task_ids": [task.id for task in items],
                    "scope": "column",
                },
                session=session,
            )
    _publish(event)
    return TaskReorderResponse(items=[task_response(task) for task in items])


async def delete_task(task_id: str, current_user: User) -> None:
    task, project, access = await _access_for_task(task_id, current_user)
    _assert_can_delete(access, task)
    children = await tasks_repository.list_by_parent(task.id)
    if children:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Remove or reassign subtasks before deleting this task",
        )

    event = None
    async with transaction() as session:
        deleted = await tasks_repository.delete(task.id, session=session)
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
        remaining = [
            item
            for item in _ordered(await _column(task.project_id, task.status.value, session))
            if item.id != task.id
        ]
        await _apply_positions([(item, index) for index, item in enumerate(remaining) if item.position != index], session)
        event = await _record(
            workspace_id=project.workspace_id,
            project_id=project.id,
            task_id=task.id,
            actor_id=access.membership.user_id,
            action=ActivityAction.DELETED.value,
            metadata={"title": task.title},
            session=session,
        )
    _publish(event)
