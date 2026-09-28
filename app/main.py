from fastapi import FastAPI
from app.core.database import check_database_connection

app = FastAPI(
    title="Syncra Backend",
    version="0.0.2",
    description="FastAPI backend for Syncra, providing REST APIs, WebSockets, authentication, and MongoDB data management for real-time project collaboration.",
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