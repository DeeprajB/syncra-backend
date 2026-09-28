from fastapi import FastAPI

app = FastAPI(title="Syncra Backend")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
