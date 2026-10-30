from fastapi import FastAPI

app = FastAPI(title="gsim")


@app.get("/health")
def health():
    return {"status": "ok"}
