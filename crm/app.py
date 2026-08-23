"""FindII web CRM — FastAPI + Jinja2 kanban pipeline for leads."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config
from core.db import LeadStore
from crm.auth import COOKIE_NAME, make_token, verify

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

KANBAN_STAGES = ("new", "contacted", "qualified", "negotiation", "won", "lost")


class AuthRequired(Exception):
    """Raised when CRM password protection is on and cookie is missing."""


def create_app(store: LeadStore) -> FastAPI:
    app = FastAPI(title="FindII CRM", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # -------------------------------------------------------------- auth

    @app.exception_handler(AuthRequired)
    async def auth_redirect(request: Request, exc: AuthRequired):
        return RedirectResponse("/login", status_code=303)

    def require_auth(request: Request) -> None:
        if not config.CRM_PASSWORD:
            return  # auth disabled when no password configured
        if not verify(config.CRM_PASSWORD, request.cookies.get(COOKIE_NAME)):
            raise AuthRequired()

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True, "service": "findii-crm"}

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {"error": ""})

    @app.post("/login")
    async def login_submit(request: Request, password: str = Form("")):
        if password != config.CRM_PASSWORD:
            return templates.TemplateResponse(
                request, "login.html",
                {"error": "Wrong password"}, status_code=401)
        resp = RedirectResponse("/board", status_code=303)
        resp.set_cookie(COOKIE_NAME, make_token(config.CRM_PASSWORD),
                        httponly=True, samesite="lax")
        return resp

    @app.get("/logout")
    async def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE_NAME)
        return resp

    # ------------------------------------------------------------ board UI

    @app.get("/", response_class=HTMLResponse)
    async def root():
        return RedirectResponse("/board", status_code=302)

    @app.get("/board", response_class=HTMLResponse)
    async def board(request: Request, _auth: None = Depends(require_auth),
                    q: str = ""):
        leads = await store.list_leads(query=q, limit=500)
        columns = []
        for stage in KANBAN_STAGES:
            cards = [l for l in leads if l.status == stage] \
                if stage != "new" else \
                [l for l in leads if l.status in ("new", "junk")]
            columns.append({"stage": stage, "cards": cards,
                            "count": len(cards)})
        junk = [l for l in leads if l.status == "junk"]
        stats = await store.stats()
        return templates.TemplateResponse(request, "board.html", {
            "columns": columns, "stats": stats, "q": q, "junk_count": len(junk),
        })

    @app.get("/lead/{lead_id}", response_class=HTMLResponse)
    async def lead_detail(request: Request, lead_id: int,
                          _auth: None = Depends(require_auth)):
        lead = await store.get_lead(lead_id)
        if not lead:
            raise HTTPException(404, "lead not found")
        notes = await store.list_notes(lead_id)
        return templates.TemplateResponse(request, "lead.html", {
            "lead": lead, "notes": notes, "stages": config.STATUSES,
        })

    @app.post("/lead/{lead_id}/status")
    async def set_status(request: Request, lead_id: int, status: str = Form(...)):
        if status not in config.STATUSES:
            raise HTTPException(422, "bad status")
        ok = await store.set_status(lead_id, 0, status)
        if not ok:
            raise HTTPException(404, "lead not found")
        if request.headers.get("accept", "").startswith("application/json") or \
                request.headers.get("x-requested-with") == "fetch":
            return {"ok": True}
        return RedirectResponse(f"/lead/{lead_id}", status_code=303)

    @app.post("/lead/{lead_id}/note")
    async def add_note(request: Request, lead_id: int, body: str = Form(...)):
        if body.strip():
            await store.add_note(lead_id, body.strip())
        return RedirectResponse(f"/lead/{lead_id}", status_code=303)

    @app.post("/lead/{lead_id}/delete")
    async def delete_lead(request: Request, lead_id: int):
        await store.delete_lead(lead_id)
        return RedirectResponse("/board", status_code=303)

    # ------------------------------------------------------------- API/CSV

    @app.get("/api/leads")
    async def api_leads(status: Optional[str] = None, min_score: int = 0,
                        q: str = "", limit: int = 100,
                        _auth: None = Depends(require_auth)):
        leads = await store.list_leads(status=status, min_score=min_score,
                                       query=q, limit=min(limit, 1000))
        return JSONResponse([l.to_dict() for l in leads])

    @app.get("/api/stats")
    async def api_stats(_auth: None = Depends(require_auth)):
        return JSONResponse(await store.stats())

    @app.get("/export.csv")
    async def export_csv(_auth: None = Depends(require_auth)):
        path = "/tmp/opencode/findii_crm_export.csv" if os.path.isdir("/tmp/opencode") \
            else "findii_crm_export.csv"
        count = await store.export_csv(path)
        with open(path, "rb") as f:
            data = f.read()
        os.remove(path)
        return Response(
            data,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=findii_leads_{count}.csv"},
        )

    return app
