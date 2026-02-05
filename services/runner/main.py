import os

from fastapi import FastAPI, Header, HTTPException

app = FastAPI()

RUNNER_TOKEN = os.getenv("RUNNER_TOKEN", "").strip()


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/generate")
def generate(authorization: str | None = Header(default=None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    token = authorization[7:].strip()
    if not RUNNER_TOKEN or token != RUNNER_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")
    return {"ok": True}
