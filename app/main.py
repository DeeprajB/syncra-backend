from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    activity,
    auth,
    comments,
    invitations,
    notifications,
    projects,
    realtime,
    tasks,
    workspaces,
)
from app.core import events
from app.core.database import check_database_connection
from app.realtime.manager import manager
from app.repositories.activities import ensure_indexes as ensure_activity_indexes
from app.repositories.comments import ensure_indexes as ensure_comment_indexes
from app.repositories.invitations import ensure_indexes as ensure_invitation_indexes
from app.repositories.notifications import ensure_indexes as ensure_notification_indexes
from app.repositories.projects import ensure_indexes as ensure_project_indexes
from app.repositories.tasks import ensure_indexes as ensure_task_indexes
from app.repositories.users import ensure_indexes as ensure_user_indexes
from app.repositories.workspace_members import ensure_indexes as ensure_member_indexes
from app.repositories.workspaces import ensure_indexes as ensure_workspace_indexes

@asynccontextmanager
async def lifespan(_app: FastAPI):
    events.subscribe(manager.publish)
    try:
        await ensure_user_indexes()
        await ensure_workspace_indexes()
        await ensure_member_indexes()
        await ensure_invitation_indexes()
        await ensure_project_indexes()
        await ensure_task_indexes()
        await ensure_activity_indexes()
        await ensure_comment_indexes()
        await ensure_notification_indexes()
        yield
    finally:
        events.unsubscribe(manager.publish)
        await manager.close_all()

app = FastAPI(
    title="Syncra Backend",
    version="0.0.8",
    description="FastAPI backend for Syncra, providing REST APIs, WebSockets, authentication, and MongoDB data management for real-time project collaboration.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "Syncra API is running"}

@app.get("/db-health")
async def db_health():
    database_healthy = await check_database_connection()

    return {
        "status": "healthy",
        "database": "connected" if database_healthy else "disconnected",
    }

app.include_router(auth.router)
app.include_router(workspaces.router)
app.include_router(invitations.router)
app.include_router(projects.router)
app.include_router(tasks.router)
app.include_router(comments.router)
app.include_router(notifications.router)
app.include_router(activity.router)
app.include_router(realtime.router)
