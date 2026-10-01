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
from crm.mini_auth import validate_init_data

SANDBOX_MIN_LEN = 4
SANDBOX_MAX_LEN = 2000

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

    @app.get("/landing", response_class=HTMLResponse)
    async def landing(request: Request):
        return templates.TemplateResponse(request, "landing.html", {})

    # ---------------------------------------- OSM lead-engine surface ----

    LEADS_DIR = os.getenv("LEADS_OUT_DIR", config.LEADS_OUT_DIR)

    @app.post("/api/parse-query")
    async def parse_query(request: Request):
        """Public: normalize a free-text demand -> structured scrape request
        (city / district / category / tags). Rule-based NLP, offline."""
        from ai.query_router import parse, display
        try:
            body = await request.json()
            q = str(body.get("query", "")).strip()
        except Exception:  # noqa: BLE001
            raise HTTPException(422, "bad payload")
        if not q:
            raise HTTPException(422, "query is empty")
        parsed = parse(q)
        return JSONResponse({**parsed, "tags": list(parsed.get("tags", ())),
                             "display": display(parsed)})

    @app.post("/api/scrape")
    async def api_scrape(request: Request):
        """Public: demand -> OSM business-lead Excel + CSV. Downloads/builds
        the city map on first use (cached afterwards). Returns file URLs."""
        from scrapers.lead_scraper import run_demand
        try:
            body = await request.json()
            query_text = str(body.get("query", "")).strip()
        except Exception:  # noqa: BLE001
            raise HTTPException(422, "bad payload")
        if len(query_text) < 4:
            raise HTTPException(422, "query too short")
        out_dir = os.path.join(os.getcwd(), LEADS_DIR)
        import asyncio as _asyncio
        try:
            result = await _asyncio.to_thread(
                run_demand, query_text, config.OSM_CACHE_DIR, out_dir,
                config.OSM_PBF_PATH)
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": str(e)[:200]})
        if result.get("error"):
            return JSONResponse({"error": result["error"],
                                 "parsed": result.get("parsed")})
        files = result.get("files", {})
        xlsx_name = os.path.basename(files.get("xlsx", ""))
        csv_name = os.path.basename(files.get("csv", ""))
        anal = {}
        for key in ("md", "html", "png_districts", "png_groups",
                    "png_density", "png_coverage"):
            p = (result.get("analyst") or {}).get(key, "")
            if p:
                anal[key.replace("png_", "")] = f"/api/export/{os.path.basename(p)}"
        return JSONResponse({
            "parsed": result.get("parsed"),
            "count": result.get("count"),
            "city": result.get("city"),
            "map_buildings": result.get("map_buildings"),
            "files": {
                "xlsx": f"/api/export/{xlsx_name}",
                "csv": f"/api/export/{csv_name}",
            },
            "analyst": anal,
            "preview": (result.get("rows") or [])[:5],
        })

    @app.get("/api/export/{filename}")
    async def api_export(filename: str):
        """Serve a generated lead export (xlsx/csv/md/html/png)."""
        safe = os.path.basename(filename)
        path = os.path.abspath(os.path.join(os.getcwd(), LEADS_DIR, safe))
        if not os.path.exists(path):
            raise HTTPException(404, "export not found")
        media = {
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".csv": "text/csv", ".md": "text/markdown; charset=utf-8",
            ".html": "text/html; charset=utf-8", ".png": "image/png",
        }.get(os.path.splitext(safe)[1].lower(), "application/octet-stream")
        with open(path, "rb") as f:
            data = f.read()
        return Response(data, media_type=media,
                        headers={"Content-Disposition":
                                 f'attachment; filename="{safe}"'})

    # --------------------------------------- public product surface ----

    def _available_maps() -> list[str]:
        try:
            return sorted(f[:-15] for f in os.listdir(config.OSM_CACHE_DIR)
                          if f.endswith(".inventory.json"))
        except OSError:
            return []

    @app.get("/api/public-stats")
    async def public_stats():
        """Public counts only — safe to call from the landing page."""
        stats = await store.stats()
        maps = _available_maps()
        buildings = 0
        for m in maps:
            try:
                import json as _json
                with open(os.path.join(config.OSM_CACHE_DIR, m + ".inventory.json"),
                          encoding="utf-8") as f:
                    buildings += len(_json.load(f).get("buildings", []))
            except Exception:  # noqa: BLE001
                pass
        return JSONResponse({
            "leads": stats["total"], "hot": stats["hot"], "today": stats["today"],
            "maps": maps, "map_buildings": buildings,
            "by_source": stats.get("by_source", {}),
        })

    @app.post("/api/try")
    async def try_extract(request: Request):
        """Public sandbox: run the REAL extraction+scoring pipeline on
        pasted text. Read-only demo — nothing is saved to the database.
        If a city is given, its OSM map is downloaded+built on first use
        (cached afterwards), then the address is matched against it."""
        from ai.extractor import LeadExtractor
        from core.geomatch import GeoMatcher
        from core.models import Lead
        from core.scoring import score_lead
        from scrapers import osm_places

        try:
            body = await request.json()
            text, city = str(body.get("text", "")), str(body.get("city", "")).strip()
        except Exception:  # noqa: BLE001
            raise HTTPException(422, "bad payload")
        if not SANDBOX_MIN_LEN <= len(text) <= SANDBOX_MAX_LEN:
            raise HTTPException(422, f"text must be {SANDBOX_MIN_LEN}-{SANDBOX_MAX_LEN} chars")

        data = await LeadExtractor(provider="regex").extract(text)
        lead = Lead(source_chat_id=0, message_id=0, source="sandbox",
                    raw_text=text[:2500])
        for key in ("deal_type", "property_type", "city", "district", "price",
                    "currency", "area_sqm", "rooms", "floor", "contact",
                    "summary", "urgency"):
            if data.get(key) is not None:
                setattr(lead, key, data[key])
        lead.is_real_estate = bool(data.get("is_real_estate"))

        geo_note = "no map loaded — type a /city in the bot to pin addresses"
        if city:
            try:
                import asyncio as _asyncio
                stats = await _asyncio.to_thread(
                    osm_places.ensure_city, city, config.OSM_CACHE_DIR,
                    config.OSM_PBF_PATH)
                first = "" if stats.get("cached") else " (map downloaded+built first-time) "
                inv_path = osm_places.inventory_path(stats["city"], config.OSM_CACHE_DIR)
                lead_geo = GeoMatcher(osm_places.load_inventory(inv_path)).match(
                    text, lead.district or "", lead.city or city)
                lead.geo_status = lead_geo["status"]
                lead.latitude, lead.longitude = lead_geo["lat"], lead_geo["lon"]
                lead.osm_ref = lead_geo["osm_ref"]
                lead.matched_address = lead_geo["matched"]
                n_b = stats.get("buildings", 0)
                guess = (" " + stats["note"]) if stats.get("note") else ""
                geo_note = (f"searched {n_b:,} real {stats.get('city', city)} map places{first}→ "
                            f"{lead.geo_status}.{guess}")
            except Exception as e:  # noqa: BLE001
                geo_note = f"map '{city}' not available ({str(e)[:120]})"
        lead.score, lead.score_reasons = score_lead(lead)
        return JSONResponse({**lead.to_dict(), "sandbox": True,
                             "provider": "regex", "geo_note": geo_note,
                             "saved": False})

    # --------------------------------------------- Telegram Mini App ----

    @app.get("/mini", response_class=HTMLResponse)
    async def mini_page(request: Request):
        return templates.TemplateResponse(request, "mini.html", {})

    def require_mini_user(request: Request) -> dict:
        try:
            return validate_init_data(
                request.headers.get("x-telegram-initdata", ""),
                config.TELEGRAM_TOKEN)
        except ValueError as e:
            raise HTTPException(401, f"mini app auth failed: {e}")

    @app.get("/mini/api/init")
    async def mini_init(request: Request, _u: dict = Depends(require_mini_user)):
        stats = await store.stats()
        maps: list[str] = []
        try:
            maps = sorted(f[:-15] for f in os.listdir(config.OSM_CACHE_DIR)
                          if f.endswith(".inventory.json"))
        except OSError:
            pass
        return JSONResponse({
            "ok": True,
            "user_name": (_u.get("user") or {}).get("first_name", ""),
            "map_city": ", ".join(maps),
            "stats": {"total": stats["total"], "hot": stats["hot"],
                      "today": stats["today"]},
        })

    @app.get("/mini/api/leads")
    async def mini_leads(request: Request, q: str = "", limit: int = 30,
                         _u: dict = Depends(require_mini_user)):
        leads = await store.list_leads(query=q, limit=min(limit, 100))
        return JSONResponse([l.to_dict() for l in leads])

    @app.post("/mini/api/mark")
    async def mini_mark(request: Request, _u: dict = Depends(require_mini_user)):
        try:
            body = await request.json()
            lead_id, status = int(body.get("id", 0)), str(body.get("status", ""))
        except Exception:  # noqa: BLE001
            raise HTTPException(422, "bad payload")
        if status not in config.STATUSES:
            raise HTTPException(422, "bad status")
        if not await store.set_status(lead_id, 0, status):
            raise HTTPException(404, "lead not found")
        return JSONResponse({"ok": True})

    ORDER_PLANS = ("Private Cloud", "On-premise License")

    @app.post("/api/order")
    async def place_order(request: Request):
        """In-page order: name + contact + plan → stored, confirmation id."""
        try:
            body = await request.json()
            name = str(body.get("name", "")).strip()
            contact = str(body.get("contact", "")).strip()
            city = str(body.get("city", "")).strip()[:80]
            plan = str(body.get("plan", "")).strip()
            message = str(body.get("message", "")).strip()[:1000]
        except Exception:  # noqa: BLE001
            raise HTTPException(422, "bad payload")
        if len(name) < 2 or len(name) > 80:
            raise HTTPException(422, "please enter your name")
        if len(contact) < 5 or len(contact) > 120:
            raise HTTPException(422, "please enter a valid phone or email")
        if plan not in ORDER_PLANS:
            raise HTTPException(422, "please choose a plan")
        oid = await store.save_order(name, contact, city, plan, message)
        return JSONResponse({"ok": True, "order_id": oid,
                             "message": f"Request #{oid} received — we reply within 24 hours."})

    @app.get("/api/orders")
    async def api_orders(_auth: None = Depends(require_auth)):
        return JSONResponse(await store.list_orders())

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
