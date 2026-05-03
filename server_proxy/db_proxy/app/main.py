from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import os
import httpx

app = FastAPI(title="DB Proxy")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
DB_PROXY_API_KEY = os.environ.get("DB_PROXY_API_KEY")

class QueryRequest(BaseModel):
    query_type: str
    table: str
    limit: int | None = None
    offset: int | None = None


@app.post("/query")
async def query(req: QueryRequest, request: Request):
    auth = request.headers.get("authorization", "")
    if DB_PROXY_API_KEY:
        if not auth.startswith("Bearer ") or auth.split()[1] != DB_PROXY_API_KEY:
            raise HTTPException(status_code=401, detail="Unauthorized")

    if SUPABASE_URL is None or SUPABASE_SERVICE_KEY is None:
        raise HTTPException(status_code=500, detail="Supabase not configured on server")

    if req.query_type != "select":
        raise HTTPException(status_code=400, detail="Only 'select' query_type is supported")

    url = SUPABASE_URL.rstrip("/") + "/rest/v1/" + req.table
    params = {"select": "*"}
    if req.limit:
        params["limit"] = req.limit
    if req.offset:
        params["offset"] = req.offset

    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, params=params, headers=headers)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=r.text[:300])

    try:
        data = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Invalid JSON from Supabase")

    return {"rows": data}
