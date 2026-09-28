from fastapi import FastAPI

app = FastAPI(
    title="Syncra Backend",
    version="0.0.1",
    description="FastAPI backend for Syncra, providing REST APIs, WebSockets, authentication, and MongoDB data management for real-time project collaboration.",
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
