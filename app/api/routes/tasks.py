from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from app.models.auth.user import User
from app.models.task import TaskPriority, TaskStatus
from app.schemas.task import (
    TaskCreate,
    TaskListResponse,
    TaskMove,
    TaskReorder,
    TaskReorderResponse,
    TaskResponse,
    TaskUpdate,
)
from app.services import task as task_service
from app.services.auth import sync_current_user

router = APIRouter(tags=["Tasks"])


@router.post(
    "/api/v1/projects/{project_id}/tasks",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a task",
    description=(
        "Creates a task at the end of the todo column. "
        "The server sets project, creator, status, position, and timestamps. "
        "Owners, admins, and members can create tasks. Viewers cannot."
    ),
)
async def create_task(
    project_id: str,
    payload: TaskCreate,
    current_user: User = Depends(sync_current_user),
) -> TaskResponse:
    return await task_service.create_task(project_id, current_user, payload)


@router.get(
    "/api/v1/projects/{project_id}/tasks",
    response_model=TaskListResponse,
    summary="List project tasks",
    description=(
        "Returns tasks for one project, ordered by column then position. "
        "Follow next_cursor until it is null to load a full board. "
        "Any workspace member, including a viewer, can list tasks."
    ),
)
async def list_tasks(
    project_id: str,
    current_user: User = Depends(sync_current_user),
    task_status: Optional[TaskStatus] = Query(default=None, alias="status"),
    assignee_id: Optional[str] = None,
    priority: Optional[TaskPriority] = None,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: Optional[str] = None,
) -> TaskListResponse:
    return await task_service.list_tasks(
        project_id,
        current_user,
        task_status=None if task_status is None else task_status.value,
        assignee_id=assignee_id,
        priority=None if priority is None else priority.value,
        limit=limit,
        cursor=cursor,
    )


@router.put(
    "/api/v1/projects/{project_id}/tasks/reorder",
    response_model=TaskReorderResponse,
    summary="Reorder a column",
    description=(
        "Replaces the order of every task currently in one status column. "
        "The id list must be exactly the tasks in that column."
    ),
)
async def reorder_tasks(
    project_id: str,
    payload: TaskReorder,
    current_user: User = Depends(sync_current_user),
) -> TaskReorderResponse:
    return await task_service.reorder_tasks(project_id, current_user, payload)


@router.get("/api/v1/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    current_user: User = Depends(sync_current_user),
) -> TaskResponse:
    return await task_service.get_task(task_id, current_user)


@router.patch("/api/v1/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: str,
    payload: TaskUpdate,
    current_user: User = Depends(sync_current_user),
) -> TaskResponse:
    return await task_service.update_task(task_id, current_user, payload)


@router.patch(
    "/api/v1/tasks/{task_id}/move",
    response_model=TaskResponse,
    summary="Move a task",
    description=(
        "Moves one task to a status column and position, then reindexes the affected columns "
        "so positions stay contiguous from zero."
    ),
)
async def move_task(
    task_id: str,
    payload: TaskMove,
    current_user: User = Depends(sync_current_user),
) -> TaskResponse:
    return await task_service.move_task(task_id, current_user, payload)


@router.delete(
    "/api/v1/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a task",
    description=(
        "Permanently deletes a task. This is not an archive. "
        "Owners and admins can delete any task. Members can delete tasks they created. "
        "Viewers cannot delete tasks. A task with subtasks must be detached first."
    ),
)
async def delete_task(
    task_id: str,
    current_user: User = Depends(sync_current_user),
) -> None:
    await task_service.delete_task(task_id, current_user)
