import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import admin
import auth
import db
import actions
import exclusions
import matching
import worklist
from auth import active_user
from rules import MODEL_INFO


def _sync_ctx() -> None:
    """Registers any CTX code present in the loaded contracts that the ctx
    table hasn't seen yet, so admins can assign it from the Access page."""
    with db.SessionLocal() as session:
        db.sync_ctx_codes(session, [c for c in db.live_contract_counts(session) if c])
        session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.validate_config()  # refuses to start insecurely (e.g. dev auth in production)
    db.ensure_migrated()    # refuses to start against a database that isn't at the latest migration
    _sync_ctx()
    yield


app = FastAPI(title="Proactive Contract Renewal", lifespan=lifespan)

# Signed-cookie session holding only the user id (see auth.py). Added before
# CORS so CORS stays the outermost layer and still decorates error responses.
app.add_middleware(
    SessionMiddleware,
    secret_key=auth.SESSION_SECRET,
    session_cookie="cr_session",
    same_site="lax",  # blocks cross-site POSTs from carrying the cookie (CSRF), allows the SSO redirect back
    https_only=auth.ENV == "production",
    max_age=auth.SESSION_MAX_AGE,
)

# Only needed while running the Vite dev server separately (port 5173).
# When the frontend is built and served from this same process, CORS is moot.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(worklist.router)
app.include_router(exclusions.router)
app.include_router(actions.router)


# Failures the rule screens expect, answered the same way wherever they arise.
@app.exception_handler(matching.CriteriaError)
async def _criteria_error(_: Request, e: matching.CriteriaError):
    return JSONResponse(status_code=422, content={"detail": str(e)})


@app.exception_handler(matching.NoData)
async def _no_data(_: Request, e: matching.NoData):
    return JSONResponse(status_code=503, content={"detail": str(e)})


@app.exception_handler(matching.VersionConflict)
async def _version_conflict(_: Request, e: matching.VersionConflict):
    return JSONResponse(status_code=409, content={"detail": "version_conflict"})


@app.get("/api/model-info")
def get_model_info(_: auth.User = Depends(active_user)):
    return MODEL_INFO


# Serve the built frontend (npm run build in /frontend -> /frontend/dist) if
# present, so the whole app can run as this single FastAPI process. During
# development, run the Vite dev server separately instead (see README).
_dist_dir = Path(__file__).parent.parent / "frontend" / "dist"
if _dist_dir.exists():
    app.mount("/", StaticFiles(directory=str(_dist_dir), html=True), name="static")
