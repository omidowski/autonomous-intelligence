import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from . import assets_api, auth, auth_api, billing_api, db, review, review_api, scheduler, usage
from .config import Settings, get_settings
from .daily_content_orchestrator import DailyContentOrchestrator
from .models import CustomContentRequest, ResearchReport, ResearchRequest
from .orchestrator import ResearchOrchestrator
from .tenancy import tenant_output_dir

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    Path(settings.daily_content_output_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.brand_assets_dir).mkdir(parents=True, exist_ok=True)
    db.init_db(settings.database_path)
    stop_event = asyncio.Event()
    task = asyncio.create_task(scheduler.scheduler_loop(stop_event, settings))
    yield
    stop_event.set()
    await task


app = FastAPI(
    title="Autonomous Intelligence API",
    version="0.1.0",
    description="Multi-agent Generative AI research and decision-support system.",
    lifespan=lifespan,
)
app.include_router(auth_api.router)
app.include_router(review_api.router)
app.include_router(assets_api.router)
app.include_router(billing_api.router)


@app.get("/media/{company_slug}/{file_path:path}")
def media(
    company_slug: str,
    file_path: str,
    current: auth.CurrentUser = Depends(auth.get_current_user),
) -> FileResponse:
    if company_slug != current.company_slug:
        raise HTTPException(status_code=403, detail="Not your company's media.")
    settings = get_settings()
    full_path = (Path(settings.daily_content_output_dir) / company_slug / file_path).resolve()
    base = (Path(settings.daily_content_output_dir) / company_slug).resolve()
    if base not in full_path.parents and full_path != base:
        raise HTTPException(status_code=403, detail="Invalid path.")
    if not full_path.is_file():
        raise HTTPException(status_code=404, detail="Not found.")
    return FileResponse(full_path)


def _media_url(company_slug: str, path: str) -> str | None:
    if not path:
        return None
    settings = get_settings()
    base = (Path(settings.daily_content_output_dir) / company_slug).resolve()
    try:
        rel = Path(path).resolve().relative_to(base)
    except ValueError:
        return None
    return "/media/" + company_slug + "/" + "/".join(rel.parts)


def _with_media_urls(data: dict[str, Any], company_slug: str) -> dict[str, Any]:
    for bundle in data.get("bundles", []):
        for asset in bundle.get("assets", []):
            asset["url"] = _media_url(company_slug, asset["path"])
    return data


def _with_review_status(
    data: dict[str, Any], settings: Settings, company_id: int, date: str
) -> dict[str, Any]:
    for index, bundle in enumerate(data.get("bundles", []), start=1):
        bundle["status"] = review.get_status(
            settings, company_id=company_id, date=date, trend_index=index
        ).model_dump(mode="json")
        bundle["variant_selections"] = db.get_selected_variants(
            settings.database_path, company_id, date, index
        )
    return data


@app.get("/health")
def health() -> dict[str, str | bool]:
    settings = get_settings()
    provider = settings.resolved_provider
    if provider == "codex":
        model = settings.codex_model or "Codex CLI default"
    elif provider == "openai":
        model = settings.openai_model
    elif provider == "openrouter":
        model = settings.openrouter_model
    else:
        model = "deterministic demo"
    return {
        "status": "ok",
        "provider": provider,
        "demo_mode": settings.demo_mode,
        "model": model,
    }


@app.post("/research", response_model=ResearchReport)
def research(
    request: ResearchRequest, current: auth.CurrentUser = Depends(auth.get_current_user)
) -> ResearchReport:
    settings = get_settings()
    company = db.get_company_by_id(settings.database_path, current.company_id)
    content_model = company["content_model"] if company is not None else None
    language = company["content_language"] if company is not None else None
    report = ResearchOrchestrator().run(request, content_model=content_model, language=language)
    db.insert_research_report(
        settings.database_path,
        company_id=current.company_id,
        query=request.query,
        report_json=report.model_dump_json(),
        mode=report.mode,
    )
    usage.record_research(settings, current.company_id)
    return report


@app.get("/research/history")
def research_history(current: auth.CurrentUser = Depends(auth.get_current_user)) -> list[dict[str, Any]]:
    settings = get_settings()
    rows = db.list_research_reports(settings.database_path, current.company_id)
    return [
        {"id": row["id"], "query": row["query"], "mode": row["mode"], "created_at": row["created_at"]}
        for row in rows
    ]


@app.get("/research/{report_id}")
def research_detail(
    report_id: int, current: auth.CurrentUser = Depends(auth.get_current_user)
) -> dict[str, Any]:
    settings = get_settings()
    row = db.get_research_report(settings.database_path, current.company_id, report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No research report with that id.")
    return json.loads(row["report_json"])


@app.get("/daily-content/dates")
def daily_content_dates(current: auth.CurrentUser = Depends(auth.get_current_user)) -> list[dict[str, Any]]:
    base = Path(get_settings().daily_content_output_dir) / current.company_slug
    if not base.is_dir():
        return []
    dates = []
    for entry in sorted(base.iterdir(), reverse=True):
        manifest = entry / "manifest.json"
        if not (entry.is_dir() and _DATE_RE.match(entry.name) and manifest.is_file()):
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        dates.append({"date": entry.name, "trend_count": len(data.get("trends", []))})
    return dates


@app.get("/daily-content/{date}")
def daily_content_detail(
    date: str, current: auth.CurrentUser = Depends(auth.get_current_user)
) -> dict[str, Any]:
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD.")
    settings = get_settings()
    manifest = tenant_output_dir(settings, current.company_slug, date) / "manifest.json"
    if not manifest.is_file():
        raise HTTPException(status_code=404, detail="No daily content report for that date.")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data = _with_media_urls(data, current.company_slug)
    return _with_review_status(data, settings, current.company_id, date)


def _company_content_kwargs(company) -> dict[str, Any]:
    """Extracts the per-company content-generation overrides
    (`DailyContentOrchestrator.run()`/`run_custom_topic()` keyword args) from
    a `companies` row - shared by the daily-content, custom-content, and
    scheduler call sites so they stay in sync."""
    if company is None:
        return {}
    return {
        "brand_voice": company["brand_voice"],
        "content_model": company["content_model"],
        "language": company["content_language"],
        "target_duration_seconds": company["target_duration_seconds"],
        "image_size": company["image_size"],
        "image_quality": company["image_quality"],
    }


@app.post("/daily-content/run")
def daily_content_run(current: auth.CurrentUser = Depends(auth.get_current_user)) -> dict[str, Any]:
    settings = get_settings()
    company = db.get_company_by_id(settings.database_path, current.company_id)
    report = DailyContentOrchestrator(settings).run(
        company_slug=current.company_slug, **_company_content_kwargs(company)
    )
    usage.record_daily_content_run(settings, current.company_id, len(report.bundles))
    data = report.model_dump(mode="json")
    data = _with_media_urls(data, current.company_slug)
    return _with_review_status(data, settings, current.company_id, report.date)


@app.get("/usage")
def usage_summary(current: auth.CurrentUser = Depends(auth.get_current_user)) -> dict[str, Any]:
    settings = get_settings()
    return usage.get_monthly_summary(settings, current.company_id)


@app.post("/custom-content/run")
def custom_content_run(
    body: CustomContentRequest, current: auth.CurrentUser = Depends(auth.get_current_user)
) -> dict[str, Any]:
    settings = get_settings()
    company = db.get_company_by_id(settings.database_path, current.company_id)
    report = DailyContentOrchestrator(settings).run_custom_topic(
        body.topic, company_slug=current.company_slug, **_company_content_kwargs(company)
    )
    usage.record_daily_content_run(settings, current.company_id, len(report.bundles))
    data = report.model_dump(mode="json")
    data = _with_media_urls(data, current.company_slug)
    return _with_review_status(data, settings, current.company_id, report.date)


@app.get("/custom-content/history")
def custom_content_history(
    current: auth.CurrentUser = Depends(auth.get_current_user),
) -> list[dict[str, Any]]:
    base = Path(get_settings().daily_content_output_dir) / current.company_slug
    if not base.is_dir():
        return []
    items = []
    for entry in sorted(base.iterdir(), reverse=True):
        manifest = entry / "manifest.json"
        if not (entry.is_dir() and entry.name.startswith("custom-") and manifest.is_file()):
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        topic = (data.get("trends") or [{}])[0].get("title", "")
        items.append({"run_id": entry.name, "topic": topic})
    return items


@app.get("/custom-content/{run_id}")
def custom_content_detail(
    run_id: str, current: auth.CurrentUser = Depends(auth.get_current_user)
) -> dict[str, Any]:
    if not run_id.startswith("custom-"):
        raise HTTPException(status_code=400, detail="Invalid custom content run id.")
    settings = get_settings()
    manifest = tenant_output_dir(settings, current.company_slug, run_id) / "manifest.json"
    if not manifest.is_file():
        raise HTTPException(status_code=404, detail="No custom content for that run id.")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data = _with_media_urls(data, current.company_slug)
    return _with_review_status(data, settings, current.company_id, run_id)


def _library_statuses(settings: Settings, company_id: int, date: str, trend_count: int) -> list[str]:
    return [
        review.get_status(
            settings, company_id=company_id, date=date, trend_index=index
        ).status.value
        for index in range(1, trend_count + 1)
    ]


@app.get("/library")
def library(current: auth.CurrentUser = Depends(auth.get_current_user)) -> list[dict[str, Any]]:
    """Unified, searchable list across all three content areas (research,
    daily content, custom content) for the Library tab - the app has no
    other place to browse research history or see daily/custom runs
    side by side. Filtering/search happens client-side (the whole list is
    small per company and this mirrors the existing hand-rolled,
    no-query-builder approach used for `/daily-content/dates` etc.)."""
    settings = get_settings()
    items: list[dict[str, Any]] = []

    for row in db.list_research_reports(settings.database_path, current.company_id):
        items.append(
            {
                "type": "research",
                "id": str(row["id"]),
                "title": row["query"],
                "created_at": row["created_at"],
                "statuses": [],
            }
        )

    base = Path(settings.daily_content_output_dir) / current.company_slug
    if base.is_dir():
        for entry in base.iterdir():
            manifest = entry / "manifest.json"
            is_custom = entry.name.startswith("custom-")
            if not (entry.is_dir() and manifest.is_file() and (is_custom or _DATE_RE.match(entry.name))):
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            trends = data.get("trends", [])
            title = trends[0]["title"] if trends else entry.name
            if len(trends) > 1:
                title = f"{title} (+{len(trends) - 1} more)"
            created_at = datetime.fromtimestamp(manifest.stat().st_mtime, tz=UTC).isoformat()
            items.append(
                {
                    "type": "custom" if is_custom else "daily",
                    "id": entry.name,
                    "title": title,
                    "created_at": created_at,
                    "statuses": _library_statuses(
                        settings, current.company_id, entry.name, len(trends)
                    ),
                }
            )

    items.sort(key=lambda item: item["created_at"], reverse=True)
    return items


@app.get("/onboarding/status")
def onboarding_status(current: auth.CurrentUser = Depends(auth.get_current_user)) -> dict[str, Any]:
    """First-run checklist for a new company - every step is derived from
    existing data (brand voice, content model, logo, past runs, team size)
    rather than tracked as its own DB state, so there's nothing new to keep
    in sync: a step is "done" exactly when the thing it describes is
    actually true, not when a user dismissed a hint."""
    settings = get_settings()
    company = db.get_company_by_id(settings.database_path, current.company_id)

    has_run = False
    base = Path(settings.daily_content_output_dir) / current.company_slug
    if base.is_dir():
        has_run = any(
            (entry / "manifest.json").is_file() for entry in base.iterdir() if entry.is_dir()
        )

    steps = [
        {
            "key": "brand_voice",
            "label": "Set your brand voice",
            "done": bool(company and company["brand_voice"]),
        },
        {
            "key": "content_model",
            "label": "Choose a content model",
            "done": bool(company and company["content_model"]),
        },
        {
            "key": "logo",
            "label": "Upload your logo",
            "done": db.get_logo_asset(settings.database_path, current.company_id) is not None,
        },
        {
            "key": "first_run",
            "label": "Generate your first content",
            "done": has_run,
        },
        {
            "key": "team",
            "label": "Invite a teammate",
            "done": len(db.list_users_for_company(settings.database_path, current.company_id)) > 1,
        },
    ]
    return {"steps": steps, "all_done": all(s["done"] for s in steps)}


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Autonomous Intelligence</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%23FF453A'/%3E%3Cstop offset='1' stop-color='%23FF2D55'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect width='100' height='100' rx='22' fill='url(%23g)'/%3E%3Ccircle cx='50' cy='50' r='16' fill='white'/%3E%3Ccircle cx='50' cy='18' r='7' fill='white' opacity='0.9'/%3E%3Ccircle cx='78' cy='50' r='7' fill='white' opacity='0.9'/%3E%3Ccircle cx='50' cy='82' r='7' fill='white' opacity='0.9'/%3E%3Ccircle cx='22' cy='50' r='7' fill='white' opacity='0.9'/%3E%3C/svg%3E" />
<style>
  :root{
    --bg:#f5f5f7; --bg-elevated:#ffffff; --bg-inset:#ffffff;
    --text:#1d1d1f; --text-secondary:#6e6e73; --text-tertiary:#86868b;
    --separator:rgba(0,0,0,0.08); --accent:#ff3b30; --accent-active:#d70015;
    --card-shadow:0 1px 2px rgba(0,0,0,0.04), 0 12px 32px rgba(0,0,0,0.06);
    --nav-bg:rgba(245,245,247,0.72);
    --green:#248a3d; --orange:#c26100; --red:#d70015; --gray:#86868b;
    --chip-bg:rgba(0,0,0,0.05); --chip-bg-hover:rgba(0,0,0,0.08);
    --track:rgba(0,0,0,0.08);
  }
  @media (prefers-color-scheme: dark){
    :root:not([data-theme="light"]){
      --bg:#000000; --bg-elevated:#1c1c1e; --bg-inset:#2c2c2e;
      --text:#f5f5f7; --text-secondary:#98989d; --text-tertiary:#7c7c80;
      --separator:rgba(255,255,255,0.09); --accent:#ff453a; --accent-active:#ff6961;
      --card-shadow:0 1px 2px rgba(0,0,0,0.3), 0 12px 32px rgba(0,0,0,0.45);
      --nav-bg:rgba(0,0,0,0.6);
      --green:#30d158; --orange:#ff9f0a; --red:#ff453a; --gray:#98989d;
      --chip-bg:rgba(255,255,255,0.08); --chip-bg-hover:rgba(255,255,255,0.14);
      --track:rgba(255,255,255,0.12);
    }
  }
  :root[data-theme="dark"]{
    --bg:#000000; --bg-elevated:#1c1c1e; --bg-inset:#2c2c2e;
    --text:#f5f5f7; --text-secondary:#98989d; --text-tertiary:#7c7c80;
    --separator:rgba(255,255,255,0.09); --accent:#ff453a; --accent-active:#ff6961;
    --card-shadow:0 1px 2px rgba(0,0,0,0.3), 0 12px 32px rgba(0,0,0,0.45);
    --nav-bg:rgba(0,0,0,0.6);
    --green:#30d158; --orange:#ff9f0a; --red:#ff453a; --gray:#98989d;
    --chip-bg:rgba(255,255,255,0.08); --chip-bg-hover:rgba(255,255,255,0.14);
    --track:rgba(255,255,255,0.12);
  }
  *{box-sizing:border-box;}
  html{-webkit-text-size-adjust:100%;}
  body{
    margin:0; background:var(--bg); color:var(--text);
    font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","SF Pro Display","Helvetica Neue",Arial,sans-serif;
    -webkit-font-smoothing:antialiased; letter-spacing:-0.01em;
  }
  a{color:var(--accent);}
  nav{
    position:sticky; top:0; z-index:20; backdrop-filter:saturate(180%) blur(20px);
    -webkit-backdrop-filter:saturate(180%) blur(20px); background:var(--nav-bg);
    border-bottom:1px solid var(--separator);
  }
  .nav-inner{
    max-width:840px; margin:0 auto; padding:14px 20px; display:flex; align-items:center; gap:10px;
  }
  .nav-mark{
    width:26px; height:26px; border-radius:8px; flex:none;
    background:linear-gradient(135deg,#ff453a,#ff2d55);
    display:flex; align-items:center; justify-content:center;
  }
  .nav-mark svg{width:15px; height:15px;}
  .nav-title{font-size:15px; font-weight:600;}
  .nav-status{
    margin-left:auto; display:flex; align-items:center; gap:6px;
    font-size:12px; color:var(--text-secondary); font-weight:500;
  }
  .dot{width:7px; height:7px; border-radius:50%; background:var(--green); flex:none;}
  .theme-toggle-btn{
    flex:none; width:30px; height:30px; border-radius:50%; border:1px solid var(--separator);
    background:var(--bg-inset); cursor:pointer; display:flex; align-items:center; justify-content:center;
    padding:0; font-size:14px; color:var(--text-secondary); transition:background .15s ease;
  }
  .theme-toggle-btn:hover{background:var(--chip-bg-hover);}
  .toast-host{
    position:fixed; top:16px; right:16px; z-index:100; display:flex; flex-direction:column; gap:8px;
    pointer-events:none;
  }
  .toast{
    background:var(--bg-elevated); color:var(--text); border:1px solid var(--separator);
    border-radius:12px; padding:11px 16px; font-size:13px; font-weight:500; box-shadow:var(--card-shadow);
    max-width:320px; opacity:0; transform:translateY(-6px); transition:opacity .2s ease, transform .2s ease;
  }
  .toast.show{opacity:1; transform:translateY(0);}
  .toast.toast-error{border-color:color-mix(in srgb, var(--red) 40%, var(--separator)); color:var(--red);}
  main{max-width:840px; margin:0 auto; padding:56px 20px 96px;}
  .hero{margin-bottom:36px;}
  .hero h1{
    font-size:40px; line-height:1.08; font-weight:700; letter-spacing:-0.02em; margin:0 0 8px;
  }
  .hero p{font-size:19px; line-height:1.4; color:var(--text-secondary); margin:0; font-weight:400;}
  .card{
    background:var(--bg-elevated); border-radius:18px; padding:22px;
    box-shadow:var(--card-shadow); border:1px solid var(--separator);
  }
  .input-card{margin-bottom:16px;}
  textarea{
    width:100%; min-height:92px; resize:vertical; border-radius:12px; padding:14px 16px;
    background:var(--bg-inset); color:var(--text); border:1px solid var(--separator);
    font-size:16px; font-family:inherit; line-height:1.45; outline:none; transition:border-color .15s ease;
  }
  textarea:focus{border-color:var(--accent);}
  input[type=email], input[type=password], input[type=text], input[type=number]{
    width:100%; border-radius:12px; padding:12px 14px;
    background:var(--bg-inset); color:var(--text); border:1px solid var(--separator);
    font-size:15px; font-family:inherit; outline:none; transition:border-color .15s ease;
  }
  input[type=email]:focus, input[type=password]:focus, input[type=text]:focus, input[type=number]:focus{border-color:var(--accent);}
  .field-label{font-size:13px; font-weight:600; color:var(--text-secondary); margin:0 0 6px 2px; display:block;}
  .field{margin-bottom:14px;}
  .auth-wrap{max-width:400px; margin:64px auto 0;}
  .auth-toggle{font-size:13px; color:var(--text-secondary); margin-top:16px; text-align:center;}
  .auth-toggle a{cursor:pointer; font-weight:600;}
  .nav-user{display:flex; align-items:center; gap:10px;}
  .link-btn{
    border:0; background:none; color:var(--text-secondary); font-size:12px; font-weight:600;
    cursor:pointer; font-family:inherit; padding:0;
  }
  .link-btn:hover{color:var(--text);}
  .schedule-row{display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-top:10px;}
  .review-row{display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-top:14px;}
  .btn-secondary{
    appearance:none; border:1px solid var(--separator); background:var(--bg-inset); color:var(--text);
    font-weight:600; font-size:13px; padding:8px 16px; border-radius:980px; cursor:pointer;
    font-family:inherit;
  }
  .btn-secondary:hover{background:var(--chip-bg);}
  .btn-danger{color:var(--red); border-color:color-mix(in srgb, var(--red) 30%, transparent);}
  .row{display:flex; align-items:center; gap:10px; margin-top:14px; flex-wrap:wrap;}
  .chips{display:flex; gap:8px; flex-wrap:wrap; margin-top:14px;}
  .chip{
    font-size:13px; padding:7px 13px; border-radius:999px; background:var(--chip-bg);
    color:var(--text-secondary); border:0; cursor:pointer; font-weight:500;
    transition:background .15s ease; font-family:inherit;
  }
  .chip:hover{background:var(--chip-bg-hover); color:var(--text);}
  .btn-primary{
    appearance:none; border:0; background:var(--accent); color:#fff; font-weight:600;
    font-size:15px; padding:11px 22px; border-radius:980px; cursor:pointer;
    transition:background .15s ease, transform .1s ease; font-family:inherit;
    display:inline-flex; align-items:center; gap:8px;
  }
  .btn-primary:hover{background:var(--accent-active);}
  .btn-primary:active{transform:scale(0.97);}
  .btn-primary:disabled{opacity:.55; cursor:default; transform:none;}
  .status-text{font-size:13px; color:var(--text-secondary); font-weight:500;}
  .spinner{
    width:14px; height:14px; border-radius:50%; border:2px solid rgba(255,255,255,0.4);
    border-top-color:#fff; animation:spin .7s linear infinite; display:none;
  }
  .spinner.on{display:inline-block;}
  @keyframes spin{to{transform:rotate(360deg);}}
  #results{display:none;}
  #results.on{display:block; animation:fade .35s ease;}
  @keyframes fade{from{opacity:0; transform:translateY(6px);} to{opacity:1; transform:translateY(0);}}
  .section{margin-top:28px;}
  .cal-header{display:flex; align-items:center; justify-content:space-between; margin-bottom:12px;}
  .cal-grid{display:grid; grid-template-columns:repeat(7,1fr); gap:6px;}
  .cal-dow{
    font-size:11px; font-weight:600; color:var(--text-tertiary); text-align:center;
    text-transform:uppercase; letter-spacing:.03em; padding-bottom:2px;
  }
  .cal-day{
    aspect-ratio:1; border-radius:10px; display:flex; flex-direction:column; align-items:center;
    justify-content:center; font-size:13px; font-weight:600; color:var(--text-tertiary);
    background:var(--bg-inset); position:relative; gap:2px;
  }
  .cal-day.empty{background:transparent;}
  .cal-day.has-content{
    background:color-mix(in srgb, var(--accent) 12%, var(--bg-inset)); color:var(--text);
    cursor:pointer; border:1px solid transparent; transition:border-color .15s ease;
  }
  .cal-day.has-content:hover{border-color:var(--accent);}
  .cal-day.selected{border-color:var(--accent); background:color-mix(in srgb, var(--accent) 20%, var(--bg-inset));}
  .cal-day.today{color:var(--accent);}
  .cal-dot{width:5px; height:5px; border-radius:50%; background:var(--accent);}
  .section-title{
    font-size:13px; font-weight:600; text-transform:uppercase; letter-spacing:.04em;
    color:var(--text-tertiary); margin:0 0 10px 2px;
  }
  .summary-text{font-size:19px; line-height:1.5; font-weight:500; margin:0;}
  .stat-grid{
    display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:10px; margin-top:16px;
  }
  .stat{
    background:var(--bg-inset); border-radius:14px; padding:14px 16px; border:1px solid var(--separator);
  }
  .stat-value{font-size:22px; font-weight:700; letter-spacing:-0.01em;}
  .stat-label{font-size:12px; color:var(--text-secondary); margin-top:2px; font-weight:500;}
  .list-card ul{margin:0; padding:0; list-style:none;}
  .list-card li{
    display:flex; gap:10px; padding:11px 0; font-size:15px; line-height:1.45;
    border-top:1px solid var(--separator);
  }
  .list-card li:first-child{border-top:0; padding-top:2px;}
  .list-icon{flex:none; width:18px; height:18px; margin-top:1px;}
  .two-col{display:grid; grid-template-columns:1fr 1fr; gap:14px;}
  @media (max-width:600px){ .two-col{grid-template-columns:1fr;} .hero h1{font-size:32px;} }
  .stack{display:flex; flex-direction:column; gap:12px;}
  .item-card{
    background:var(--bg-elevated); border-radius:16px; padding:16px 18px; border:1px solid var(--separator);
  }
  .item-head{display:flex; align-items:flex-start; justify-content:space-between; gap:12px;}
  .item-title{font-size:15px; font-weight:600; line-height:1.35;}
  .pill{
    flex:none; font-size:11px; font-weight:700; padding:4px 10px; border-radius:999px;
    text-transform:uppercase; letter-spacing:.03em; white-space:nowrap;
  }
  .pill-supported{background:color-mix(in srgb, var(--green) 16%, transparent); color:var(--green);}
  .pill-mixed{background:color-mix(in srgb, var(--orange) 16%, transparent); color:var(--orange);}
  .pill-insufficient{background:color-mix(in srgb, var(--gray) 18%, transparent); color:var(--gray);}
  .pill-contradicted{background:color-mix(in srgb, var(--red) 16%, transparent); color:var(--red);}
  .pill-draft{background:var(--chip-bg); color:var(--text-secondary);}
  .pill-pending_review{background:color-mix(in srgb, var(--orange) 16%, transparent); color:var(--orange);}
  .pill-approved{background:color-mix(in srgb, var(--green) 16%, transparent); color:var(--green);}
  .pill-rejected{background:color-mix(in srgb, var(--red) 16%, transparent); color:var(--red);}
  .item-meta{font-size:12px; color:var(--text-tertiary); margin-top:4px; font-weight:500;}
  .library-row{cursor:pointer; transition:border-color .15s ease;}
  .library-row:hover{border-color:var(--accent);}
  .onboard-step{display:flex; align-items:center; gap:10px; font-size:14px;}
  .onboard-dot{
    width:18px; height:18px; border-radius:50%; flex:none; border:1.5px solid var(--separator);
    display:flex; align-items:center; justify-content:center;
  }
  .onboard-dot.done{background:var(--green); border-color:var(--green);}
  .onboard-dot.done::after{content:''; width:5px; height:8px; border:solid #fff; border-width:0 1.5px 1.5px 0; transform:rotate(45deg) translate(-1px,-1px);}
  .onboard-label{flex:1;}
  .onboard-label.done{color:var(--text-secondary); text-decoration:line-through;}
  .onboard-go{background:none; border:0; color:var(--accent); font-size:13px; font-weight:600; cursor:pointer; padding:0;}
  .type-badge{
    font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.03em;
    padding:3px 8px; border-radius:999px; background:var(--chip-bg); color:var(--text-secondary); flex:none;
  }
  .item-body{font-size:14px; color:var(--text-secondary); line-height:1.55; margin-top:8px; white-space:pre-wrap;}
  .clamp{max-height:4.7em; overflow:hidden; position:relative;}
  .clamp::after{
    content:''; position:absolute; left:0; right:0; bottom:0; height:1.6em;
    background:linear-gradient(transparent, var(--bg-elevated));
  }
  .more-btn{
    border:0; background:none; color:var(--accent); font-size:13px; font-weight:600;
    padding:8px 0 0; cursor:pointer; font-family:inherit;
  }
  .confidence-track{
    height:4px; border-radius:2px; background:var(--track); margin-top:10px; overflow:hidden;
  }
  .confidence-fill{height:100%; border-radius:2px; background:var(--accent);}
  .empty-state{
    text-align:center; padding:40px 20px; color:var(--text-tertiary); font-size:14px;
  }
  .footer-meta{
    margin-top:32px; text-align:center; font-size:12px; color:var(--text-tertiary);
  }
  .error-card{
    border:1px solid color-mix(in srgb, var(--red) 30%, transparent);
    background:color-mix(in srgb, var(--red) 8%, transparent);
    border-radius:14px; padding:16px 18px; margin-top:16px; font-size:14px; color:var(--red);
  }
  .tab-bar{
    display:inline-flex; gap:2px; padding:3px; border-radius:12px; background:var(--chip-bg);
    margin-bottom:28px;
  }
  .tab-btn{
    border:0; background:none; font-family:inherit; font-size:14px; font-weight:600;
    padding:8px 16px; border-radius:9px; cursor:pointer; color:var(--text-secondary);
    transition:background .15s ease, color .15s ease;
  }
  .tab-btn.active{background:var(--bg-elevated); color:var(--text); box-shadow:var(--card-shadow);}
  select{
    appearance:none; border-radius:12px; padding:11px 36px 11px 16px; background:var(--bg-inset);
    color:var(--text); border:1px solid var(--separator); font-size:14px; font-family:inherit;
    font-weight:500; cursor:pointer;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20' fill='%2386868b'%3E%3Cpath d='M5.5 7.5l4.5 4.5 4.5-4.5' stroke='%2386868b' stroke-width='1.5' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
    background-repeat:no-repeat; background-position:right 12px center;
  }
  .trend-card{
    background:var(--bg-elevated); border-radius:18px; padding:20px; border:1px solid var(--separator);
    box-shadow:var(--card-shadow);
  }
  .trend-head{display:flex; align-items:flex-start; justify-content:space-between; gap:12px; margin-bottom:6px;}
  .trend-title{font-size:19px; font-weight:700; letter-spacing:-0.01em; line-height:1.3;}
  .trend-summary{font-size:14px; color:var(--text-secondary); line-height:1.5; margin:6px 0 18px;}
  .subsection-title{
    font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.04em;
    color:var(--text-tertiary); margin:20px 0 10px;
  }
  .subsection-title:first-of-type{margin-top:0;}
  .media-row{display:flex; gap:14px; flex-wrap:wrap;}
  .media-box{position:relative; border-radius:14px; overflow:hidden; background:var(--bg-inset); border:1px solid var(--separator);}
  .media-box img, .media-box video{display:block; width:100%; height:100%; object-fit:cover;}
  .cover-box{width:140px; height:200px; flex:none;}
  .video-box{width:220px; height:auto; flex:none;}
  .video-box video{aspect-ratio:9/16;}
  .asset-box{width:100px; height:100px; flex:none;}
  .asset-delete-btn{
    position:absolute; top:6px; right:6px; width:20px; height:20px; border:none; border-radius:50%;
    background:rgba(0,0,0,0.65); color:#fff; font-size:13px; line-height:1; cursor:pointer;
    display:flex; align-items:center; justify-content:center; padding:0;
  }
  .placeholder-flag{
    position:absolute; top:8px; left:8px; font-size:9px; font-weight:700; text-transform:uppercase;
    letter-spacing:.03em; padding:3px 7px; border-radius:6px; background:rgba(0,0,0,0.65); color:#fff;
  }
  .placeholder-flag-right{left:auto; right:8px;}
  .attribution-flag{
    position:absolute; bottom:0; left:0; right:0; font-size:10px; font-weight:500;
    padding:5px 8px; background:rgba(0,0,0,0.55); color:#fff; line-height:1.3;
  }
  audio{width:100%; height:36px; margin-top:8px;}
  .slide-grid{display:grid; grid-template-columns:repeat(auto-fill,minmax(120px,1fr)); gap:12px;}
  .slide-card{border-radius:12px; overflow:hidden; border:1px solid var(--separator); background:var(--bg-inset);}
  .slide-card img{width:100%; aspect-ratio:9/16; object-fit:cover; display:block;}
  .slide-caption{font-size:12px; padding:8px 10px; color:var(--text-secondary); line-height:1.4;}
  .post-card{border-top:1px solid var(--separator); padding:12px 0;}
  .post-card:first-child{border-top:0; padding-top:0;}
  .post-card-head{display:flex; align-items:center; justify-content:space-between; gap:8px;}
  .platform-badge{
    font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.03em;
    padding:3px 8px; border-radius:999px; background:var(--chip-bg); color:var(--text-secondary);
  }
  .variant-toggle{display:flex; gap:4px;}
  .variant-btn{
    appearance:none; border:1px solid var(--separator); background:var(--bg-inset); color:var(--text-secondary);
    font-size:11px; font-weight:700; width:22px; height:22px; border-radius:6px; cursor:pointer; padding:0;
  }
  .variant-btn.active{background:var(--accent); border-color:var(--accent); color:#fff;}
  .mockup-card{
    margin-top:8px; padding:10px 12px; border-radius:10px; background:var(--bg-inset);
    border:1px solid var(--separator); border-left-width:3px; border-left-style:solid;
  }
  .mockup-head{display:flex; align-items:center; gap:8px; margin-bottom:2px;}
  .mockup-avatar{width:16px; height:16px; border-radius:50%; flex:none;}
  .mockup-name{font-size:12px; font-weight:600;}
  .mockup-time{font-size:11px; color:var(--text-tertiary); margin-left:auto;}
  .post-text{font-size:14px; line-height:1.5; margin-top:6px;}
  .hashtags{font-size:13px; color:var(--accent); margin-top:4px;}
  .script-line{font-size:14px; line-height:1.6; color:var(--text-secondary); margin-bottom:6px;}
  .script-line b{color:var(--text); font-weight:600;}
  .trend-select-row{display:flex; align-items:center; gap:10px; margin-bottom:20px; flex-wrap:wrap;}
</style>
</head>
<body>
<nav>
  <div class="nav-inner">
    <div class="nav-mark">
      <svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="3.4" fill="white"/><circle cx="12" cy="4" r="1.8" fill="white" opacity=".85"/><circle cx="20" cy="12" r="1.8" fill="white" opacity=".85"/><circle cx="12" cy="20" r="1.8" fill="white" opacity=".85"/><circle cx="4" cy="12" r="1.8" fill="white" opacity=".85"/></svg>
    </div>
    <div class="nav-title">Autonomous Intelligence</div>
    <div class="nav-status" id="navStatus"><span class="dot"></span><span>Checking…</span></div>
    <button class="theme-toggle-btn" id="themeToggleBtn" type="button" title="Toggle theme" aria-label="Toggle theme"></button>
    <div class="nav-user" id="navUser" style="display:none"></div>
  </div>
</nav>

<div id="toastHost" class="toast-host"></div>

<main id="authGate" style="display:none">
  <div class="auth-wrap">
    <div class="hero" style="margin-bottom:24px">
      <h1 id="authTitle" style="font-size:28px">Sign in</h1>
    </div>
    <div class="card">
      <div class="field" id="companyNameField" style="display:none">
        <label class="field-label">Company name</label>
        <input type="text" id="authCompanyName" placeholder="Acme Inc." />
      </div>
      <div class="field">
        <label class="field-label">Email</label>
        <input type="email" id="authEmail" placeholder="you@company.com" />
      </div>
      <div class="field">
        <label class="field-label">Password</label>
        <input type="password" id="authPassword" placeholder="••••••••" />
      </div>
      <div class="row">
        <button class="btn-primary" id="authSubmitBtn" type="button">
          <span class="spinner" id="authSpinner"></span>
          <span id="authSubmitLabel">Sign in</span>
        </button>
        <span class="status-text" id="authStatus"></span>
      </div>
    </div>
    <div id="authErrorBox"></div>
    <div class="auth-toggle" id="authToggle">No account? <a id="authToggleLink">Sign up</a></div>
  </div>
</main>

<main id="appMain" style="display:none">
  <div class="card input-card" id="onboardingCard" style="display:none">
    <div class="item-head" style="margin-bottom:2px">
      <div class="section-title" style="margin:0">Get set up</div>
      <button class="link-btn" id="onboardingDismissBtn" type="button">Dismiss</button>
    </div>
    <div id="onboardingSteps" class="stack" style="margin-top:10px"></div>
  </div>

  <div class="tab-bar">
    <button class="tab-btn active" type="button" data-tab="research">Research</button>
    <button class="tab-btn" type="button" data-tab="daily-content">Daily Content</button>
    <button class="tab-btn" type="button" data-tab="custom-content">Custom Content</button>
    <button class="tab-btn" type="button" data-tab="library">Library</button>
  </div>

  <div id="researchView">
  <div class="hero">
    <h1>Ask a research question.</h1>
    <p>Multi-agent research, retrieval-augmented evidence, and fact-checked answers — orchestrated end to end.</p>
  </div>

  <div class="card input-card">
    <textarea id="q" placeholder="What do you want to research?">How will agentic AI change enterprise software?</textarea>
    <div class="chips">
      <button class="chip" type="button" data-q="What are the strongest enterprise use cases for AI agents?">Enterprise use cases</button>
      <button class="chip" type="button" data-q="How should companies evaluate the reliability of AI agents?">Evaluating reliability</button>
      <button class="chip" type="button" data-q="What are the main risks of autonomous AI agents in production?">Production risks</button>
    </div>
    <div class="row">
      <button class="btn-primary" id="runBtn" type="button">
        <span class="spinner" id="spinner"></span>
        <span id="runLabel">Run research</span>
      </button>
      <span class="status-text" id="status"></span>
    </div>
  </div>

  <div id="errorBox"></div>

  <div id="results">
    <div class="section">
      <div class="section-title">Executive summary</div>
      <div class="card">
        <p class="summary-text" id="summaryText"></p>
        <div class="stat-grid" id="statGrid"></div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">Key findings</div>
      <div class="card list-card"><ul id="findingsList"></ul></div>
    </div>

    <div class="section two-col">
      <div>
        <div class="section-title">Opportunities</div>
        <div class="card list-card"><ul id="oppsList"></ul></div>
      </div>
      <div>
        <div class="section-title">Risks</div>
        <div class="card list-card"><ul id="risksList"></ul></div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">Fact checks</div>
      <div class="stack" id="factChecks"></div>
    </div>

    <div class="section">
      <div class="section-title">Evidence</div>
      <div class="stack" id="evidenceList"></div>
    </div>

    <div class="section">
      <div class="section-title">Recommendations</div>
      <div class="card list-card"><ul id="recsList"></ul></div>
    </div>

    <div class="footer-meta" id="footerMeta"></div>
  </div>
  </div>

  <div id="dailyContentView" style="display:none">
    <div class="hero">
      <h1>Today's content, drafted.</h1>
      <p>Scans today's top news trends and drafts social posts, a short/reel video, a story, and a podcast script for each — automatically.</p>
    </div>

    <div class="card input-card">
      <div class="trend-select-row">
        <button class="btn-primary" id="dcRunBtn" type="button">
          <span class="spinner" id="dcSpinner"></span>
          <span id="dcRunLabel">Run today's content</span>
        </button>
        <select id="dcDateSelect"><option value="">No saved reports yet</option></select>
        <span class="status-text" id="dcStatus"></span>
      </div>
      <p class="status-text" style="margin:0">Each run scans live news and drafts new content — this can take a minute or two and costs real API usage.</p>
    </div>

    <div class="card input-card" id="dcCalendarCard">
      <div class="cal-header">
        <button class="link-btn" id="dcCalPrev" type="button">‹ Prev</button>
        <div class="section-title" id="dcCalLabel" style="margin:0"></div>
        <button class="link-btn" id="dcCalNext" type="button">Next ›</button>
      </div>
      <div class="cal-grid" id="dcCalGrid"></div>
    </div>

    <div class="card input-card" id="usageCard">
      <div class="section-title" style="margin:0 0 8px">This month's usage</div>
      <div class="stat-grid" id="usageStatGrid"></div>
    </div>

    <div class="card input-card" id="scheduleCard" style="display:none">
      <label class="field-label" style="display:flex; align-items:center; gap:8px; margin:0">
        <input type="checkbox" id="scheduleEnabled" /> Run automatically each day
      </label>
      <div class="schedule-row">
        <span class="status-text">at</span>
        <input type="number" id="scheduleHour" min="0" max="23" style="width:70px" placeholder="HH" />
        <span class="status-text">:</span>
        <input type="number" id="scheduleMinute" min="0" max="59" style="width:70px" placeholder="MM" />
        <span class="status-text">UTC</span>
        <button class="btn-secondary" id="scheduleSaveBtn" type="button">Save</button>
        <span class="status-text" id="scheduleStatus"></span>
      </div>
    </div>

    <div class="card input-card" id="brandVoiceCard" style="display:none">
      <label class="field-label">Brand voice</label>
      <textarea id="brandVoiceText" style="min-height:60px" placeholder="e.g. Warm, plain-spoken, a little playful - like a knowledgeable friend, never corporate-sounding. Short sentences, no jargon."></textarea>
      <div class="row">
        <button class="btn-secondary" id="brandVoiceSaveBtn" type="button">Save</button>
        <span class="status-text" id="brandVoiceStatus"></span>
      </div>
      <p class="status-text" style="margin:8px 0 0">Leave empty to use the default Apple-style marketing voice.</p>
    </div>

    <div class="card input-card" id="contentModelCard" style="display:none">
      <label class="field-label">Content model</label>
      <select id="contentModelSelect" style="width:100%"><option value="">App default (OpenAI)</option></select>
      <div class="row">
        <button class="btn-secondary" id="contentModelSaveBtn" type="button">Save</button>
        <span class="status-text" id="contentModelStatus"></span>
      </div>
      <p class="status-text" style="margin:8px 0 0">Routes content generation through OpenRouter with this model - requires an OpenRouter API key/balance regardless of the app's default provider.</p>
    </div>

    <div class="card input-card" id="contentSettingsCard" style="display:none">
      <div class="section-title" style="margin:0 0 8px">More content settings</div>
      <div class="field">
        <label class="field-label">Content language</label>
        <input type="text" id="csLanguage" placeholder="e.g. German, Spanish, Japanese — leave empty for English" />
      </div>
      <div class="field">
        <label class="field-label">Target audio/video length (seconds)</label>
        <input type="number" id="csDuration" min="10" max="180" placeholder="e.g. 30 — leave empty for the model's own default" />
      </div>
      <div class="field">
        <label class="field-label">Image size</label>
        <select id="csImageSize"><option value="">App default</option></select>
      </div>
      <div class="field">
        <label class="field-label">Image quality</label>
        <select id="csImageQuality"><option value="">App default</option></select>
      </div>
      <div class="row">
        <button class="btn-secondary" id="contentSettingsSaveBtn" type="button">Save</button>
        <span class="status-text" id="contentSettingsStatus"></span>
      </div>
    </div>

    <div class="card input-card" id="teamCard" style="display:none">
      <div class="section-title" style="margin:0 0 8px">Team</div>
      <div id="teamList" class="status-text"></div>
      <div class="row" id="inviteRow" style="display:none; margin-top:14px">
        <input type="email" id="inviteEmail" placeholder="teammate@company.com" style="max-width:220px" />
        <input type="password" id="invitePassword" placeholder="temporary password" style="max-width:180px" />
        <button class="btn-secondary" id="inviteBtn" type="button">Invite</button>
        <span class="status-text" id="inviteStatus"></span>
      </div>
      <p class="status-text" id="inviteHint" style="margin:8px 0 0; display:none">No email is sent - share this password with your teammate yourself.</p>
    </div>

    <div class="card input-card" id="assetsCard" style="display:none">
      <div class="section-title" style="margin:0 0 8px">Brand assets</div>
      <div id="assetsList" class="media-row" style="margin-bottom:14px"></div>
      <div class="row" id="assetsUploadRow" style="display:none">
        <input type="file" id="assetsFileInput" accept="image/png,image/jpeg,image/webp" />
        <label class="field-label" style="display:flex; align-items:center; gap:6px; margin:0">
          <input type="checkbox" id="assetsIsLogo" /> Use as watermark logo
        </label>
        <button class="btn-secondary" id="assetsUploadBtn" type="button">Upload</button>
        <span class="status-text" id="assetsStatus"></span>
      </div>
      <p class="status-text" style="margin:8px 0 0">The logo is automatically overlaid on generated cover/slide images (not on placeholders).</p>
    </div>

    <div id="dcErrorBox"></div>

    <div id="dcResults"></div>
  </div>

  <div id="customContentView" style="display:none">
    <div class="hero">
      <h1>Create content on any topic.</h1>
      <p>Type any subject — not just today's news — and get the same social posts, short/reel video, story, and podcast script, generated on demand.</p>
    </div>

    <div class="card input-card">
      <textarea id="ccTopic" placeholder="What should the content be about?">The benefits of a four-day work week</textarea>
      <div class="row">
        <button class="btn-primary" id="ccRunBtn" type="button">
          <span class="spinner" id="ccSpinner"></span>
          <span id="ccRunLabel">Generate content</span>
        </button>
        <select id="ccHistorySelect"><option value="">No past runs yet</option></select>
        <span class="status-text" id="ccStatus"></span>
      </div>
      <p class="status-text" style="margin:8px 0 0">Each run drafts new content for this topic — this can take a minute or two and costs real API usage.</p>
    </div>

    <div id="ccErrorBox"></div>

    <div id="ccResults"></div>
  </div>

  <div id="libraryView" style="display:none">
    <div class="hero">
      <h1>Everything you've created.</h1>
      <p>Search and filter across research, daily content, and custom content.</p>
    </div>

    <div class="card input-card">
      <div class="row">
        <input type="text" id="libSearch" placeholder="Search by title, topic, or query" style="flex:1; min-width:200px" />
        <select id="libTypeFilter">
          <option value="">All types</option>
          <option value="research">Research</option>
          <option value="daily">Daily Content</option>
          <option value="custom">Custom Content</option>
        </select>
        <select id="libStatusFilter">
          <option value="">All statuses</option>
          <option value="draft">Draft</option>
          <option value="pending_review">Pending review</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
        </select>
      </div>
    </div>

    <div id="libErrorBox"></div>
    <div id="libResults"></div>
  </div>
</main>

<script>
const iconCheck = '<svg class="list-icon" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" fill="var(--accent)" opacity=".14"/><path d="M7 12.5l3 3 7-7" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>';
const iconDot = (color) => '<svg class="list-icon" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4" fill="' + color + '"/></svg>';
const iconQuestion = '<svg class="list-icon" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" fill="var(--gray)" opacity=".14"/><text x="12" y="16.5" font-size="12" font-weight="700" text-anchor="middle" fill="var(--gray)">?</text></svg>';

function esc(s){
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

function pct(x){ return Math.round(x * 100) + '%'; }

function systemPrefersDark(){
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
}

function applyTheme(theme){
  const root = document.documentElement;
  if (theme === 'light' || theme === 'dark'){
    root.setAttribute('data-theme', theme);
  } else {
    root.removeAttribute('data-theme');
  }
  const isDark = theme === 'dark' || (theme !== 'light' && systemPrefersDark());
  document.getElementById('themeToggleBtn').textContent = isDark ? '☀️' : '🌙';
}

function initTheme(){
  let stored = null;
  try{ stored = localStorage.getItem('ai-theme'); }catch(e){}
  applyTheme(stored);
}
initTheme();

document.getElementById('themeToggleBtn').addEventListener('click', () => {
  let stored = null;
  try{ stored = localStorage.getItem('ai-theme'); }catch(e){}
  const currentlyDark = stored === 'dark' || (stored !== 'light' && systemPrefersDark());
  const next = currentlyDark ? 'light' : 'dark';
  try{ localStorage.setItem('ai-theme', next); }catch(e){}
  applyTheme(next);
});

let toastSeq = 0;
function showToast(message, type){
  const host = document.getElementById('toastHost');
  const id = 'toast-' + (toastSeq++);
  const el = document.createElement('div');
  el.className = 'toast' + (type === 'error' ? ' toast-error' : '');
  el.id = id;
  el.textContent = message;
  host.appendChild(el);
  requestAnimationFrame(() => el.classList.add('show'));
  setTimeout(() => {
    el.classList.remove('show');
    setTimeout(() => el.remove(), 250);
  }, 3200);
}

function verdictMeta(v){
  return {
    supported: {label:'Supported', cls:'pill-supported'},
    mixed: {label:'Mixed', cls:'pill-mixed'},
    insufficient: {label:'Insufficient', cls:'pill-insufficient'},
    contradicted: {label:'Contradicted', cls:'pill-contradicted'}
  }[v] || {label:v, cls:'pill-insufficient'};
}

document.querySelectorAll('.chip').forEach(btn => {
  btn.addEventListener('click', () => {
    document.getElementById('q').value = btn.dataset.q;
  });
});

async function checkHealth(){
  const el = document.getElementById('navStatus');
  try{
    const r = await fetch('/health');
    const d = await r.json();
    el.innerHTML = '<span class="dot" style="background:' + (d.status === 'ok' ? 'var(--green)' : 'var(--red)') + '"></span><span>' + esc(d.provider) + (d.demo_mode && d.provider !== 'demo' ? ' · demo' : '') + '</span>';
  }catch(e){
    el.innerHTML = '<span class="dot" style="background:var(--red)"></span><span>offline</span>';
  }
}
checkHealth();

let currentUser = null;
let authMode = 'login';

function setAuthMode(mode){
  authMode = mode;
  document.getElementById('companyNameField').style.display = mode === 'signup' ? '' : 'none';
  document.getElementById('authTitle').textContent = mode === 'signup' ? 'Create your account' : 'Sign in';
  document.getElementById('authSubmitLabel').textContent = mode === 'signup' ? 'Sign up' : 'Sign in';
  document.getElementById('authToggle').innerHTML = mode === 'signup'
    ? 'Already have an account? <a id="authToggleLink">Sign in</a>'
    : 'No account? <a id="authToggleLink">Sign up</a>';
  document.getElementById('authToggleLink').addEventListener('click', () => setAuthMode(mode === 'signup' ? 'login' : 'signup'));
  document.getElementById('authErrorBox').innerHTML = '';
}
document.getElementById('authToggleLink').addEventListener('click', () => setAuthMode('signup'));

function renderNavUser(){
  const el = document.getElementById('navUser');
  if (!currentUser){ el.style.display = 'none'; el.innerHTML = ''; return; }
  el.style.display = '';
  el.innerHTML = '<span class="status-text">' + esc(currentUser.company.name) + ' · ' + esc(currentUser.email) + '</span>' +
    '<button class="link-btn" id="logoutBtn" type="button">Log out</button>';
  document.getElementById('logoutBtn').addEventListener('click', logout);
}

async function checkAuth(){
  try{
    const r = await fetch('/auth/me');
    const d = r.status === 200 ? await r.json() : null;
    currentUser = d && d.email ? d : null;
  }catch(e){
    currentUser = null;
  }
  renderNavUser();
  document.getElementById('authGate').style.display = currentUser ? 'none' : '';
  document.getElementById('appMain').style.display = currentUser ? '' : 'none';
  if (currentUser){
    renderScheduleCard();
    renderBrandVoiceCard();
    loadContentModelCard();
    loadContentSettingsCard();
    loadUsage();
    loadTeam();
    loadAssetsCard();
    loadOnboarding();
  }
}

async function authSubmit(){
  const btn = document.getElementById('authSubmitBtn');
  const spinner = document.getElementById('authSpinner');
  const status = document.getElementById('authStatus');
  const errorBox = document.getElementById('authErrorBox');
  const email = document.getElementById('authEmail').value.trim();
  const password = document.getElementById('authPassword').value;
  const companyName = document.getElementById('authCompanyName').value.trim();

  errorBox.innerHTML = ''; status.textContent = '';
  if (!email || !password){ status.textContent = 'Enter email and password.'; return; }
  if (authMode === 'signup' && !companyName){ status.textContent = 'Enter a company name.'; return; }

  btn.disabled = true; spinner.classList.add('on');
  try{
    const url = authMode === 'signup' ? '/auth/signup' : '/auth/login';
    const body = authMode === 'signup' ? {company_name: companyName, email, password} : {email, password};
    const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    if (!r.ok){
      let detail = 'Request failed (' + r.status + ').';
      try { const errData = await r.json(); if (errData.detail) detail = JSON.stringify(errData.detail); } catch(e){}
      throw new Error(detail);
    }
    await checkAuth();
  }catch(e){
    errorBox.innerHTML = '<div class="error-card">' + esc(e.message || String(e)) + '</div>';
  }finally{
    btn.disabled = false; spinner.classList.remove('on');
  }
}
document.getElementById('authSubmitBtn').addEventListener('click', authSubmit);

async function logout(){
  await fetch('/auth/logout', {method:'POST'});
  currentUser = null;
  dcDatesLoaded = false;
  await checkAuth();
}

checkAuth();

function renderResults(data){
  document.getElementById('summaryText').textContent = data.executive_summary;

  const stats = [
    {label:'Evidence', value:data.evaluation.evidence_count},
    {label:'Claim support', value:pct(data.evaluation.claim_support_rate)},
    {label:'Contradictions', value:pct(data.evaluation.contradiction_rate)},
    {label:'Avg. confidence', value:pct(data.evaluation.average_evidence_confidence)},
    {label:'Latency', value:data.evaluation.latency_seconds.toFixed(1) + 's'}
  ];
  document.getElementById('statGrid').innerHTML = stats.map(s =>
    '<div class="stat"><div class="stat-value">' + esc(s.value) + '</div><div class="stat-label">' + esc(s.label) + '</div></div>'
  ).join('');

  document.getElementById('findingsList').innerHTML = data.analysis.key_findings.map(f =>
    '<li>' + iconCheck + '<span>' + esc(f) + '</span></li>'
  ).join('') || '<li><span>No findings.</span></li>';

  document.getElementById('oppsList').innerHTML = data.analysis.opportunities.map(o =>
    '<li>' + iconDot('var(--green)') + '<span>' + esc(o) + '</span></li>'
  ).join('') || '<li><span>None identified.</span></li>';

  document.getElementById('risksList').innerHTML = data.analysis.risks.map(r =>
    '<li>' + iconDot('var(--red)') + '<span>' + esc(r) + '</span></li>'
  ).join('') || '<li><span>None identified.</span></li>';

  const openQ = data.analysis.open_questions.map(q =>
    '<li>' + iconQuestion + '<span>' + esc(q) + '</span></li>'
  ).join('');
  if (openQ){
    document.getElementById('risksList').innerHTML += openQ;
  }

  document.getElementById('factChecks').innerHTML = data.fact_checks.map(fc => {
    const v = verdictMeta(fc.verdict);
    return '<div class="item-card"><div class="item-head"><div class="item-title">' + esc(fc.claim) + '</div><span class="pill ' + v.cls + '">' + v.label + '</span></div>' +
      '<div class="item-body">' + esc(fc.rationale) + '</div>' +
      '<div class="confidence-track"><div class="confidence-fill" style="width:' + Math.round(fc.confidence*100) + '%"></div></div></div>';
  }).join('') || '<div class="empty-state">No claims were checked.</div>';

  document.getElementById('evidenceList').innerHTML = data.evidence.map((ev, i) => {
    const bodyId = 'ev-body-' + i;
    return '<div class="item-card"><div class="item-head"><div class="item-title">' + esc(ev.title) + '</div><span class="pill pill-insufficient" style="color:var(--text-secondary); background:var(--chip-bg)">' + Math.round(ev.relevance*100) + '% relevant</span></div>' +
      '<div class="item-meta">' + esc(ev.source) + ' · ' + Math.round(ev.confidence*100) + '% confidence</div>' +
      '<div class="item-body clamp" id="' + bodyId + '">' + esc(ev.content) + '</div>' +
      '<button class="more-btn" onclick="toggleClamp(\\'' + bodyId + '\\', this)">Show more</button></div>';
  }).join('') || '<div class="empty-state">No evidence collected.</div>';

  document.getElementById('recsList').innerHTML = data.recommendations.map(r =>
    '<li>' + iconCheck + '<span>' + esc(r) + '</span></li>'
  ).join('') || '<li><span>No recommendations.</span></li>';

  const generated = new Date(data.generated_at);
  document.getElementById('footerMeta').textContent =
    'Mode: ' + data.mode + ' · Generated ' + generated.toLocaleString();

  document.getElementById('results').classList.add('on');
}

function toggleClamp(id, btn){
  const el = document.getElementById(id);
  const collapsed = el.classList.toggle('clamp');
  btn.textContent = collapsed ? 'Show more' : 'Show less';
}

async function runResearch(){
  const q = document.getElementById('q').value.trim();
  const btn = document.getElementById('runBtn');
  const spinner = document.getElementById('spinner');
  const label = document.getElementById('runLabel');
  const status = document.getElementById('status');
  const errorBox = document.getElementById('errorBox');
  const results = document.getElementById('results');

  if (q.length < 5){
    status.textContent = 'Enter a longer question.';
    return;
  }

  btn.disabled = true; spinner.classList.add('on'); label.textContent = 'Researching…';
  status.textContent = 'Planning → researching → fact-checking → analyzing…';
  errorBox.innerHTML = ''; results.classList.remove('on');

  try{
    const r = await fetch('/research', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({query:q})
    });
    if (!r.ok){
      let detail = 'Request failed (' + r.status + ').';
      try { const errData = await r.json(); if (errData.detail) detail = JSON.stringify(errData.detail); } catch(e){}
      throw new Error(detail);
    }
    const data = await r.json();
    renderResults(data);
    status.textContent = 'Done';
  }catch(e){
    errorBox.innerHTML = '<div class="error-card">' + esc(e.message || String(e)) + '</div>';
    status.textContent = '';
  }finally{
    btn.disabled = false; spinner.classList.remove('on'); label.textContent = 'Run research';
  }
}

document.getElementById('runBtn').addEventListener('click', runResearch);
document.getElementById('q').addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') runResearch();
});

function switchTab(tab){
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  document.getElementById('researchView').style.display = tab === 'research' ? '' : 'none';
  document.getElementById('dailyContentView').style.display = tab === 'daily-content' ? '' : 'none';
  document.getElementById('customContentView').style.display = tab === 'custom-content' ? '' : 'none';
  document.getElementById('libraryView').style.display = tab === 'library' ? '' : 'none';
  if (tab === 'daily-content' && !dcDatesLoaded){
    loadDailyContentDates();
  }
  if (tab === 'custom-content' && !ccHistoryLoaded){
    loadCustomContentHistory();
  }
  if (tab === 'library' && !libraryLoaded){
    loadLibrary();
  }
}

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});

let dcDatesLoaded = false;
let ccHistoryLoaded = false;

function platformLabel(p){
  return {
    x:'X', instagram:'Instagram', facebook:'Facebook', linkedin:'LinkedIn', tiktok:'TikTok',
    threads:'Threads', bluesky:'Bluesky', pinterest:'Pinterest', reddit:'Reddit', youtube:'YouTube'
  }[p] || p;
}

function mediaBox(asset, extraClass, isVideo){
  if (!asset || !asset.url) return '';
  let flags = '';
  if (asset.is_placeholder){
    flags = '<span class="placeholder-flag">Placeholder</span>';
  } else {
    if (asset.source === 'stock'){
      flags += '<span class="placeholder-flag" style="background:rgba(10,132,255,0.85)">Stock photo</span>';
    }
    if (asset.source === 'generated' && isVideo){
      flags += '<span class="placeholder-flag" style="background:rgba(191,90,242,0.85)">AI video</span>';
    }
    if (isVideo && asset.is_silent){
      flags += '<span class="placeholder-flag placeholder-flag-right" style="background:rgba(255,159,10,0.9)" title="Real video, no narration audio">Silent</span>';
    }
  }
  const attribution = asset.source === 'stock' && asset.attribution
    ? '<span class="attribution-flag">' + esc(asset.attribution) + '</span>'
    : '';
  const el = isVideo
    ? '<video src="' + esc(asset.url) + '" controls preload="metadata"></video>'
    : '<img src="' + esc(asset.url) + '" alt="" />';
  return '<div class="media-box ' + extraClass + '">' + el + flags + attribution + '</div>';
}

function renderScheduleCard(){
  const card = document.getElementById('scheduleCard');
  if (!currentUser || currentUser.role !== 'owner'){ card.style.display = 'none'; return; }
  card.style.display = '';
  const company = currentUser.company;
  document.getElementById('scheduleEnabled').checked = !!company.daily_run_enabled;
  document.getElementById('scheduleHour').value = company.daily_run_hour_utc ?? '';
  document.getElementById('scheduleMinute').value = company.daily_run_minute_utc ?? '';
}

async function saveSchedule(){
  const status = document.getElementById('scheduleStatus');
  const enabled = document.getElementById('scheduleEnabled').checked;
  const hour = document.getElementById('scheduleHour').value;
  const minute = document.getElementById('scheduleMinute').value;
  status.textContent = 'Saving…';
  try{
    const r = await fetch('/auth/me/schedule', {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({enabled, hour_utc: hour === '' ? null : Number(hour), minute_utc: minute === '' ? null : Number(minute)})
    });
    if (!r.ok){
      let detail = 'Request failed (' + r.status + ').';
      try { const errData = await r.json(); if (errData.detail) detail = JSON.stringify(errData.detail); } catch(e){}
      throw new Error(detail);
    }
    const company = await r.json();
    currentUser.company = company;
    status.textContent = 'Saved.';
    showToast('Saved.');
  }catch(e){
    status.textContent = e.message || String(e);
    showToast(e.message || String(e), 'error');
  }
}
document.getElementById('scheduleSaveBtn').addEventListener('click', saveSchedule);

function renderBrandVoiceCard(){
  const card = document.getElementById('brandVoiceCard');
  if (!currentUser || currentUser.role !== 'owner'){ card.style.display = 'none'; return; }
  card.style.display = '';
  document.getElementById('brandVoiceText').value = currentUser.company.brand_voice || '';
}

async function saveBrandVoice(){
  const status = document.getElementById('brandVoiceStatus');
  const brandVoice = document.getElementById('brandVoiceText').value;
  status.textContent = 'Saving…';
  try{
    const r = await fetch('/auth/me/brand-voice', {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({brand_voice: brandVoice})
    });
    if (!r.ok){
      let detail = 'Request failed (' + r.status + ').';
      try { const errData = await r.json(); if (errData.detail) detail = JSON.stringify(errData.detail); } catch(e){}
      throw new Error(detail);
    }
    const company = await r.json();
    currentUser.company = company;
    status.textContent = 'Saved.';
    showToast('Saved.');
  }catch(e){
    status.textContent = e.message || String(e);
    showToast(e.message || String(e), 'error');
  }
}
document.getElementById('brandVoiceSaveBtn').addEventListener('click', saveBrandVoice);

let contentModelChoicesLoaded = false;

async function loadContentModelCard(){
  const card = document.getElementById('contentModelCard');
  if (!currentUser || currentUser.role !== 'owner'){ card.style.display = 'none'; return; }
  card.style.display = '';

  if (!contentModelChoicesLoaded){
    try{
      const r = await fetch('/auth/content-model-choices');
      if (r.ok){
        const choices = await r.json();
        const select = document.getElementById('contentModelSelect');
        select.innerHTML = '<option value="">App default (OpenAI)</option>' +
          Object.entries(choices).map(([id, label]) => '<option value="' + esc(id) + '">' + esc(label) + '</option>').join('');
        contentModelChoicesLoaded = true;
      }
    }catch(e){}
  }
  document.getElementById('contentModelSelect').value = currentUser.company.content_model || '';
}

async function saveContentModel(){
  const status = document.getElementById('contentModelStatus');
  const contentModel = document.getElementById('contentModelSelect').value;
  status.textContent = 'Saving…';
  try{
    const r = await fetch('/auth/me/content-model', {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({content_model: contentModel})
    });
    if (!r.ok){
      let detail = 'Request failed (' + r.status + ').';
      try { const errData = await r.json(); if (errData.detail) detail = JSON.stringify(errData.detail); } catch(e){}
      throw new Error(detail);
    }
    const company = await r.json();
    currentUser.company = company;
    status.textContent = 'Saved.';
    showToast('Saved.');
  }catch(e){
    status.textContent = e.message || String(e);
    showToast(e.message || String(e), 'error');
  }
}
document.getElementById('contentModelSaveBtn').addEventListener('click', saveContentModel);

let contentSettingsChoicesLoaded = false;

function fillSelect(selectEl, values, defaultLabel){
  selectEl.innerHTML = '<option value="">' + esc(defaultLabel) + '</option>' +
    values.map(v => '<option value="' + esc(v) + '">' + esc(v) + '</option>').join('');
}

async function loadContentSettingsCard(){
  const card = document.getElementById('contentSettingsCard');
  if (!currentUser || currentUser.role !== 'owner'){ card.style.display = 'none'; return; }
  card.style.display = '';

  if (!contentSettingsChoicesLoaded){
    try{
      const r = await fetch('/auth/content-settings-choices');
      if (r.ok){
        const choices = await r.json();
        fillSelect(document.getElementById('csImageSize'), choices.image_size, 'App default');
        fillSelect(document.getElementById('csImageQuality'), choices.image_quality, 'App default');
        contentSettingsChoicesLoaded = true;
      }
    }catch(e){}
  }

  const company = currentUser.company;
  document.getElementById('csLanguage').value = company.content_language || '';
  document.getElementById('csDuration').value = company.target_duration_seconds || '';
  document.getElementById('csImageSize').value = company.image_size || '';
  document.getElementById('csImageQuality').value = company.image_quality || '';
}

async function saveContentSettings(){
  const status = document.getElementById('contentSettingsStatus');
  const duration = document.getElementById('csDuration').value;
  status.textContent = 'Saving…';
  try{
    const r = await fetch('/auth/me/content-settings', {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        content_language: document.getElementById('csLanguage').value,
        target_duration_seconds: duration === '' ? null : Number(duration),
        image_size: document.getElementById('csImageSize').value,
        image_quality: document.getElementById('csImageQuality').value
      })
    });
    if (!r.ok){
      let detail = 'Request failed (' + r.status + ').';
      try { const errData = await r.json(); if (errData.detail) detail = JSON.stringify(errData.detail); } catch(e){}
      throw new Error(detail);
    }
    const company = await r.json();
    currentUser.company = company;
    status.textContent = 'Saved.';
    showToast('Saved.');
  }catch(e){
    status.textContent = e.message || String(e);
    showToast(e.message || String(e), 'error');
  }
}
document.getElementById('contentSettingsSaveBtn').addEventListener('click', saveContentSettings);

async function loadUsage(){
  try{
    const r = await fetch('/usage');
    if (!r.ok) return;
    const d = await r.json();
    const stats = [
      {label:'Research queries', value:d.research_calls},
      {label:'Content runs', value:d.content_runs},
      {label:'Trends generated', value:d.trends_generated},
      {label:'Est. cost (' + d.period + ')', value:'$' + d.estimated_cost_usd.toFixed(2)}
    ];
    document.getElementById('usageStatGrid').innerHTML = stats.map(s =>
      '<div class="stat"><div class="stat-value">' + esc(s.value) + '</div><div class="stat-label">' + esc(s.label) + '</div></div>'
    ).join('');
  }catch(e){}
}

async function loadTeam(){
  const listEl = document.getElementById('teamList');
  const inviteRow = document.getElementById('inviteRow');
  const inviteHint = document.getElementById('inviteHint');
  document.getElementById('teamCard').style.display = '';
  inviteRow.style.display = currentUser.role === 'owner' ? 'flex' : 'none';
  inviteHint.style.display = currentUser.role === 'owner' ? '' : 'none';
  try{
    const r = await fetch('/auth/team');
    if (!r.ok) return;
    const members = await r.json();
    listEl.innerHTML = members.map(m => esc(m.email) + ' <span style="color:var(--text-tertiary)">(' + esc(m.role) + ')</span>').join('<br>');
  }catch(e){
    listEl.textContent = 'Could not load team.';
  }
}

async function sendInvite(){
  const status = document.getElementById('inviteStatus');
  const email = document.getElementById('inviteEmail').value.trim();
  const password = document.getElementById('invitePassword').value;
  if (!email || !password){ status.textContent = 'Enter email and password.'; return; }
  status.textContent = 'Inviting…';
  try{
    const r = await fetch('/auth/invite', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({email, password})
    });
    if (!r.ok){
      let detail = 'Request failed (' + r.status + ').';
      try { const errData = await r.json(); if (errData.detail) detail = JSON.stringify(errData.detail); } catch(e){}
      throw new Error(detail);
    }
    document.getElementById('inviteEmail').value = '';
    document.getElementById('invitePassword').value = '';
    status.textContent = 'Invited.';
    showToast('Invited.');
    loadTeam();
  }catch(e){
    status.textContent = e.message || String(e);
    showToast(e.message || String(e), 'error');
  }
}
document.getElementById('inviteBtn').addEventListener('click', sendInvite);

function renderAssetsList(assets){
  const listEl = document.getElementById('assetsList');
  if (!assets.length){
    listEl.innerHTML = '<span class="status-text">No brand assets yet.</span>';
    return;
  }
  listEl.innerHTML = assets.map(a => {
    const isImage = a.content_type && a.content_type.startsWith('image/');
    const el = isImage
      ? '<img src="' + esc(a.url) + '" alt="" />'
      : '<span class="status-text">' + esc(a.filename) + '</span>';
    const flag = a.is_logo ? '<span class="placeholder-flag" style="background:rgba(10,132,255,0.85)">Logo</span>' : '';
    const del = currentUser.role === 'owner'
      ? '<button class="asset-delete-btn" type="button" onclick="deleteAsset(' + a.id + ')" title="Delete">×</button>'
      : '';
    return '<div class="media-box asset-box">' + el + flag + del + '</div>';
  }).join('');
}

async function loadAssetsCard(){
  const card = document.getElementById('assetsCard');
  card.style.display = '';
  document.getElementById('assetsUploadRow').style.display = currentUser.role === 'owner' ? 'flex' : 'none';
  try{
    const r = await fetch('/assets');
    if (!r.ok) return;
    const assets = await r.json();
    renderAssetsList(assets);
  }catch(e){
    document.getElementById('assetsList').textContent = 'Could not load brand assets.';
  }
}

async function uploadAsset(){
  const status = document.getElementById('assetsStatus');
  const fileInput = document.getElementById('assetsFileInput');
  const isLogo = document.getElementById('assetsIsLogo').checked;
  const file = fileInput.files[0];
  if (!file){ status.textContent = 'Choose a file first.'; return; }
  status.textContent = 'Uploading…';
  try{
    const formData = new FormData();
    formData.append('file', file);
    const r = await fetch('/assets/upload?is_logo=' + (isLogo ? 'true' : 'false'), {
      method:'POST', body: formData
    });
    if (!r.ok){
      let detail = 'Request failed (' + r.status + ').';
      try { const errData = await r.json(); if (errData.detail) detail = JSON.stringify(errData.detail); } catch(e){}
      throw new Error(detail);
    }
    fileInput.value = '';
    document.getElementById('assetsIsLogo').checked = false;
    status.textContent = 'Uploaded.';
    showToast('Uploaded.');
    loadAssetsCard();
  }catch(e){
    status.textContent = e.message || String(e);
    showToast(e.message || String(e), 'error');
  }
}
document.getElementById('assetsUploadBtn').addEventListener('click', uploadAsset);

async function deleteAsset(id){
  const status = document.getElementById('assetsStatus');
  try{
    const r = await fetch('/assets/' + id, { method:'DELETE' });
    if (!r.ok) throw new Error('Request failed (' + r.status + ').');
    loadAssetsCard();
  }catch(e){
    status.textContent = e.message || String(e);
    showToast(e.message || String(e), 'error');
  }
}

const ONBOARDING_STEP_TARGETS = {
  brand_voice: 'brandVoiceCard', content_model: 'contentModelCard', logo: 'assetsCard',
  first_run: 'dcRunBtn', team: 'teamCard'
};

function goToOnboardingStep(key){
  switchTab('daily-content');
  const targetId = ONBOARDING_STEP_TARGETS[key];
  if (targetId){
    setTimeout(() => {
      const el = document.getElementById(targetId);
      if (el) el.scrollIntoView({behavior:'smooth', block:'center'});
    }, 50);
  }
}

function onboardingDismissKey(){
  return 'ai-onboarding-dismissed-' + (currentUser && currentUser.company ? currentUser.company.slug : '');
}

function renderOnboarding(data){
  const card = document.getElementById('onboardingCard');
  let dismissed = false;
  try{ dismissed = localStorage.getItem(onboardingDismissKey()) === '1'; }catch(e){}
  if (data.all_done || dismissed){ card.style.display = 'none'; return; }
  card.style.display = '';
  document.getElementById('onboardingSteps').innerHTML = data.steps.map(s =>
    '<div class="onboard-step">' +
      '<span class="onboard-dot' + (s.done ? ' done' : '') + '"></span>' +
      '<span class="onboard-label' + (s.done ? ' done' : '') + '">' + esc(s.label) + '</span>' +
      (s.done ? '' : '<button class="onboard-go" type="button" onclick="goToOnboardingStep(\\'' + s.key + '\\')">Go</button>') +
    '</div>'
  ).join('');
}

async function loadOnboarding(){
  try{
    const r = await fetch('/onboarding/status');
    if (!r.ok) return;
    renderOnboarding(await r.json());
  }catch(e){}
}

document.getElementById('onboardingDismissBtn').addEventListener('click', () => {
  try{ localStorage.setItem(onboardingDismissKey(), '1'); }catch(e){}
  document.getElementById('onboardingCard').style.display = 'none';
});

const REVIEW_TRANSITIONS = {
  draft: [{action:'submit-for-review', label:'Submit for review'}],
  pending_review: [{action:'approve', label:'Approve'}, {action:'reject', label:'Reject'}],
  rejected: [{action:'submit-for-review', label:'Resubmit for review'}],
  approved: [{action:'publish', label:'Publish (stub)'}]
};
const REVIEW_STATUS_LABEL = {draft:'Draft', pending_review:'Pending review', approved:'Approved', rejected:'Rejected'};
const REVIEW_ACTION_RESULT_STATUS = {
  'submit-for-review':'pending_review', approve:'approved', reject:'rejected'
};

async function reviewAction(context, date, trendIndex, action, btn){
  btn.disabled = true;
  const errorBoxId = context === 'custom' ? 'ccErrorBox' : 'dcErrorBox';
  try{
    const r = await fetch('/daily-content/' + date + '/trend/' + trendIndex + '/' + action, {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({})
    });
    if (!r.ok){
      let detail = 'Request failed (' + r.status + ').';
      try { const errData = await r.json(); if (errData.detail) detail = JSON.stringify(errData.detail); } catch(e){}
      throw new Error(detail);
    }
    showToast(action === 'publish' ? 'Published (stub).' : REVIEW_STATUS_LABEL[REVIEW_ACTION_RESULT_STATUS[action]] + '.');
    if (context === 'custom'){ loadCustomContentRun(date); } else { loadDailyContentDate(date); }
  }catch(e){
    document.getElementById(errorBoxId).innerHTML = '<div class="error-card">' + esc(e.message || String(e)) + '</div>';
    showToast(e.message || String(e), 'error');
    btn.disabled = false;
  }
}

function reviewControls(context, date, trendIndex, bundleStatus){
  const s = (bundleStatus && bundleStatus.status) || 'draft';
  const pill = '<span class="pill pill-' + s + '">' + (REVIEW_STATUS_LABEL[s] || s) + '</span>';
  const buttons = (REVIEW_TRANSITIONS[s] || []).map(t =>
    '<button class="btn-secondary" type="button" onclick="reviewAction(\\'' + context + '\\', \\'' + date + '\\', ' + trendIndex + ', \\'' + t.action + '\\', this)">' + esc(t.label) + '</button>'
  ).join('');
  return '<div class="review-row">' + pill + buttons + '</div>';
}

const PLATFORM_COLOR = {
  x:'#000000', instagram:'#E1306C', facebook:'#1877F2', linkedin:'#0A66C2', tiktok:'#000000',
  threads:'#000000', bluesky:'#0085FF', pinterest:'#E60023', reddit:'#FF4500', youtube:'#FF0000'
};

function postGroup(context, date, trendIndex, platform, variants, selectedVariant){
  const color = PLATFORM_COLOR[platform] || 'var(--accent)';
  const hasVariants = variants.length > 1;
  const active = variants.find(v => v.variant === selectedVariant) || variants[0];
  const toggle = hasVariants
    ? '<div class="variant-toggle">' + variants.map(v =>
        '<button class="variant-btn' + (v.variant === (active.variant || 'A') ? ' active' : '') + '" type="button" ' +
        'onclick="selectVariant(\\'' + context + '\\', \\'' + date + '\\', ' + trendIndex + ', \\'' + platform + '\\', \\'' + v.variant + '\\')">' + esc(v.variant) + '</button>'
      ).join('') + '</div>'
    : '';
  return '<div class="post-card">' +
    '<div class="post-card-head"><span class="platform-badge">' + esc(platformLabel(platform)) + '</span>' + toggle + '</div>' +
    '<div class="mockup-card" style="border-left-color:' + color + '">' +
      '<div class="mockup-head"><span class="mockup-avatar" style="background:' + color + '"></span>' +
      '<span class="mockup-name">Your Company</span><span class="mockup-time">now</span></div>' +
      '<div class="post-text">' + esc(active.text) + '</div>' +
      (active.hashtags && active.hashtags.length ? '<div class="hashtags">' + active.hashtags.map(esc).join(' ') + '</div>' : '') +
    '</div>' +
  '</div>';
}

async function selectVariant(context, date, trendIndex, platform, variant){
  try{
    const r = await fetch('/daily-content/' + date + '/trend/' + trendIndex + '/variant', {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({platform, variant})
    });
    if (!r.ok) throw new Error('Request failed (' + r.status + ').');
    if (context === 'custom'){ loadCustomContentRun(date); } else { loadDailyContentDate(date); }
  }catch(e){
    showToast(e.message || String(e), 'error');
  }
}

function renderDailyContent(data, resultsElId, context){
  resultsElId = resultsElId || 'dcResults';
  context = context || 'daily';
  const bundles = data.bundles || [];
  if (!bundles.length){
    document.getElementById(resultsElId).innerHTML = '<div class="empty-state">No trends in this report.</div>';
    return;
  }
  document.getElementById(resultsElId).innerHTML = bundles.map((b, i) => {
    const trendIndex = i + 1;
    const assets = b.assets || [];
    const cover = assets.find(a => a.kind === 'image' && a.path.endsWith('cover.png'));
    const video = assets.find(a => a.kind === 'video');
    const audio = assets.find(a => a.kind === 'audio');

    const postsByPlatform = {};
    (b.social_posts || []).forEach(p => {
      (postsByPlatform[p.platform] = postsByPlatform[p.platform] || []).push(p);
    });
    const variantSelections = b.variant_selections || {};
    const posts = Object.keys(postsByPlatform).map(platform =>
      postGroup(context, data.date, trendIndex, platform, postsByPlatform[platform], variantSelections[platform] || 'A')
    ).join('');

    const short = b.short_script || {};
    const shortHtml =
      '<div class="script-line"><b>Hook: </b>' + esc(short.hook) + '</div>' +
      (short.beats || []).map(beat => '<div class="script-line">' + esc(beat) + '</div>').join('') +
      '<div class="script-line"><b>CTA: </b>' + esc(short.cta) + '</div>';

    const slides = (b.story && b.story.slides) || [];
    const slideHtml = slides.map(s => {
      const asset = assets.find(a => a.kind === 'image' && a.path.endsWith('slide-' + s.slide_number + '.png'));
      const img = asset && asset.url ? '<img src="' + esc(asset.url) + '" alt="" />' : '';
      return '<div class="slide-card">' + img + '<div class="slide-caption">' + esc(s.text) + '</div></div>';
    }).join('');

    const podcast = b.podcast_script || {};
    const podcastHtml =
      '<div class="script-line"><b>' + esc(podcast.title) + '</b></div>' +
      '<div class="script-line">' + esc(podcast.intro) + '</div>' +
      (podcast.segments || []).map(seg => '<div class="script-line">' + esc(seg) + '</div>').join('') +
      '<div class="script-line">' + esc(podcast.outro) + '</div>' +
      (audio && audio.url ? '<audio src="' + esc(audio.url) + '" controls></audio>' : '');

    const shots = (b.video_script && b.video_script.shots) || [];
    const shotsHtml = shots.map(s =>
      '<div class="script-line"><b>Shot ' + s.shot_number + ' (' + s.duration_seconds + 's): </b>' + esc(s.description) +
      (s.on_screen_text ? ' — "' + esc(s.on_screen_text) + '"' : '') + '</div>'
    ).join('');

    return '<div class="trend-card" style="margin-bottom:20px">' +
      '<div class="trend-head"><div class="trend-title">' + esc(b.trend.title) + '</div>' +
      '<span class="pill pill-insufficient" style="color:var(--text-secondary); background:var(--chip-bg)">' + esc(b.trend.category) + '</span></div>' +
      '<div class="trend-summary">' + esc(b.trend.summary) + '</div>' +
      reviewControls(context, data.date, trendIndex, b.status) +
      '<div class="media-row">' + mediaBox(cover, 'cover-box', false) + mediaBox(video, 'video-box', true) + '</div>' +
      '<div class="subsection-title">Social posts</div>' + posts +
      '<div class="subsection-title">Short / reel script</div>' + shortHtml +
      '<div class="subsection-title">Story</div><div class="slide-grid">' + slideHtml + '</div>' +
      '<div class="subsection-title">Podcast</div>' + podcastHtml +
      '<div class="subsection-title">Video shot list</div>' + shotsHtml +
      '</div>';
  }).join('');
}

let dcDatesByDate = {};
let dcSelectedDate = null;
const now = new Date();
let calYear = now.getFullYear();
let calMonth = now.getMonth(); // 0-11

async function loadDailyContentDates(selectDate){
  const select = document.getElementById('dcDateSelect');
  try{
    const r = await fetch('/daily-content/dates');
    const dates = await r.json();
    dcDatesLoaded = true;
    dcDatesByDate = {};
    dates.forEach(d => { dcDatesByDate[d.date] = d; });
    if (!dates.length){
      select.innerHTML = '<option value="">No saved reports yet</option>';
      renderCalendar();
      return;
    }
    select.innerHTML = dates.map(d => '<option value="' + esc(d.date) + '">' + esc(d.date) + ' · ' + d.trend_count + ' trends</option>').join('');
    const target = selectDate || dates[0].date;
    select.value = target;
    const [y, m] = target.split('-').map(Number);
    calYear = y; calMonth = m - 1;
    renderCalendar();
    loadDailyContentDate(target);
  }catch(e){
    select.innerHTML = '<option value="">Could not load reports</option>';
  }
}

const MONTH_NAMES = ['January','February','March','April','May','June','July','August','September','October','November','December'];

function renderCalendar(){
  document.getElementById('dcCalLabel').textContent = MONTH_NAMES[calMonth] + ' ' + calYear;
  const grid = document.getElementById('dcCalGrid');
  const firstDay = new Date(calYear, calMonth, 1);
  const startOffset = (firstDay.getDay() + 6) % 7; // Monday-first
  const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
  const todayStr = new Date().toISOString().slice(0, 10);

  let html = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(d => '<div class="cal-dow">' + d + '</div>').join('');
  for (let i = 0; i < startOffset; i++) html += '<div class="cal-day empty"></div>';
  for (let day = 1; day <= daysInMonth; day++){
    const dateStr = calYear + '-' + String(calMonth + 1).padStart(2, '0') + '-' + String(day).padStart(2, '0');
    const entry = dcDatesByDate[dateStr];
    const classes = ['cal-day'];
    if (entry) classes.push('has-content');
    if (dateStr === todayStr) classes.push('today');
    if (dateStr === dcSelectedDate) classes.push('selected');
    const dot = entry ? '<span class="cal-dot"></span>' : '';
    const onclick = entry ? ' onclick="selectCalendarDate(\\'' + dateStr + '\\')"' : '';
    html += '<div class="' + classes.join(' ') + '"' + onclick + ' title="' + (entry ? entry.trend_count + ' trends' : '') + '">' + day + dot + '</div>';
  }
  grid.innerHTML = html;
}

function selectCalendarDate(dateStr){
  dcSelectedDate = dateStr;
  document.getElementById('dcDateSelect').value = dateStr;
  renderCalendar();
  loadDailyContentDate(dateStr);
}

document.getElementById('dcCalPrev').addEventListener('click', () => {
  calMonth -= 1;
  if (calMonth < 0){ calMonth = 11; calYear -= 1; }
  renderCalendar();
});
document.getElementById('dcCalNext').addEventListener('click', () => {
  calMonth += 1;
  if (calMonth > 11){ calMonth = 0; calYear += 1; }
  renderCalendar();
});

async function loadDailyContentDate(date){
  if (!date) return;
  dcSelectedDate = date;
  renderCalendar();
  const status = document.getElementById('dcStatus');
  status.textContent = 'Loading ' + date + '…';
  try{
    const r = await fetch('/daily-content/' + date);
    if (!r.ok) throw new Error('Request failed (' + r.status + ').');
    const data = await r.json();
    renderDailyContent(data);
    status.textContent = 'Mode: ' + data.mode;
  }catch(e){
    document.getElementById('dcErrorBox').innerHTML = '<div class="error-card">' + esc(e.message || String(e)) + '</div>';
    status.textContent = '';
  }
}

document.getElementById('dcDateSelect').addEventListener('change', (e) => loadDailyContentDate(e.target.value));

async function runDailyContent(){
  const btn = document.getElementById('dcRunBtn');
  const spinner = document.getElementById('dcSpinner');
  const label = document.getElementById('dcRunLabel');
  const status = document.getElementById('dcStatus');
  const errorBox = document.getElementById('dcErrorBox');

  btn.disabled = true; spinner.classList.add('on'); label.textContent = 'Scanning trends…';
  status.textContent = 'Scanning trends → writing content → rendering media…';
  errorBox.innerHTML = '';

  try{
    const r = await fetch('/daily-content/run', {method:'POST'});
    if (!r.ok){
      let detail = 'Request failed (' + r.status + ').';
      try { const errData = await r.json(); if (errData.detail) detail = JSON.stringify(errData.detail); } catch(e){}
      throw new Error(detail);
    }
    const data = await r.json();
    renderDailyContent(data);
    status.textContent = 'Done · mode: ' + data.mode;
    loadDailyContentDates(data.date);
  }catch(e){
    errorBox.innerHTML = '<div class="error-card">' + esc(e.message || String(e)) + '</div>';
    status.textContent = '';
  }finally{
    btn.disabled = false; spinner.classList.remove('on'); label.textContent = "Run today's content";
  }
}

document.getElementById('dcRunBtn').addEventListener('click', runDailyContent);

async function loadCustomContentHistory(selectRunId){
  const select = document.getElementById('ccHistorySelect');
  try{
    const r = await fetch('/custom-content/history');
    const runs = await r.json();
    ccHistoryLoaded = true;
    if (!runs.length){
      select.innerHTML = '<option value="">No past runs yet</option>';
      return;
    }
    select.innerHTML = '<option value="">— select a past run —</option>' +
      runs.map(run => '<option value="' + esc(run.run_id) + '">' + esc(run.topic) + '</option>').join('');
    if (selectRunId){
      select.value = selectRunId;
      loadCustomContentRun(selectRunId);
    }
  }catch(e){
    select.innerHTML = '<option value="">Could not load history</option>';
  }
}

async function loadCustomContentRun(runId){
  if (!runId) return;
  const status = document.getElementById('ccStatus');
  status.textContent = 'Loading…';
  try{
    const r = await fetch('/custom-content/' + runId);
    if (!r.ok) throw new Error('Request failed (' + r.status + ').');
    const data = await r.json();
    renderDailyContent(data, 'ccResults', 'custom');
    status.textContent = 'Mode: ' + data.mode;
  }catch(e){
    document.getElementById('ccErrorBox').innerHTML = '<div class="error-card">' + esc(e.message || String(e)) + '</div>';
    status.textContent = '';
  }
}

document.getElementById('ccHistorySelect').addEventListener('change', (e) => loadCustomContentRun(e.target.value));

async function runCustomContent(){
  const topic = document.getElementById('ccTopic').value.trim();
  const btn = document.getElementById('ccRunBtn');
  const spinner = document.getElementById('ccSpinner');
  const label = document.getElementById('ccRunLabel');
  const status = document.getElementById('ccStatus');
  const errorBox = document.getElementById('ccErrorBox');

  if (topic.length < 3){
    status.textContent = 'Enter a topic.';
    return;
  }

  btn.disabled = true; spinner.classList.add('on'); label.textContent = 'Generating…';
  status.textContent = 'Writing content → rendering media…';
  errorBox.innerHTML = '';

  try{
    const r = await fetch('/custom-content/run', {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({topic})
    });
    if (!r.ok){
      let detail = 'Request failed (' + r.status + ').';
      try { const errData = await r.json(); if (errData.detail) detail = JSON.stringify(errData.detail); } catch(e){}
      throw new Error(detail);
    }
    const data = await r.json();
    renderDailyContent(data, 'ccResults', 'custom');
    status.textContent = 'Done · mode: ' + data.mode;
    loadCustomContentHistory(data.date);
  }catch(e){
    errorBox.innerHTML = '<div class="error-card">' + esc(e.message || String(e)) + '</div>';
    status.textContent = '';
  }finally{
    btn.disabled = false; spinner.classList.remove('on'); label.textContent = 'Generate content';
  }
}

document.getElementById('ccRunBtn').addEventListener('click', runCustomContent);

let libraryLoaded = false;
let libraryItems = [];

const LIBRARY_TYPE_LABEL = {research:'Research', daily:'Daily Content', custom:'Custom Content'};

function libraryStatusPills(statuses){
  if (!statuses || !statuses.length) return '';
  const counts = {};
  statuses.forEach(s => { counts[s] = (counts[s] || 0) + 1; });
  return Object.keys(counts).map(s =>
    '<span class="pill pill-' + esc(s) + '">' + (REVIEW_STATUS_LABEL[s] || s) + (counts[s] > 1 ? ' ×' + counts[s] : '') + '</span>'
  ).join(' ');
}

function renderLibrary(){
  const search = document.getElementById('libSearch').value.trim().toLowerCase();
  const typeFilter = document.getElementById('libTypeFilter').value;
  const statusFilter = document.getElementById('libStatusFilter').value;

  const filtered = libraryItems.filter(item => {
    if (typeFilter && item.type !== typeFilter) return false;
    if (statusFilter && !(item.statuses || []).includes(statusFilter)) return false;
    if (search && item.title.toLowerCase().indexOf(search) === -1) return false;
    return true;
  });

  const resultsEl = document.getElementById('libResults');
  if (!filtered.length){
    resultsEl.innerHTML = '<div class="empty-state">Nothing matches yet.</div>';
    return;
  }
  resultsEl.innerHTML = '<div class="stack">' + filtered.map(item => {
    const when = new Date(item.created_at).toLocaleString();
    return '<div class="item-card library-row" onclick="viewLibraryItem(\\'' + item.type + '\\', \\'' + item.id + '\\')">' +
      '<div class="item-head">' +
        '<div class="item-title">' + esc(item.title) + '</div>' +
        '<span class="type-badge">' + LIBRARY_TYPE_LABEL[item.type] + '</span>' +
      '</div>' +
      '<div class="item-meta">' + esc(when) + '</div>' +
      (item.statuses.length ? '<div style="margin-top:8px">' + libraryStatusPills(item.statuses) + '</div>' : '') +
    '</div>';
  }).join('') + '</div>';
}

async function loadLibrary(){
  const errorBox = document.getElementById('libErrorBox');
  errorBox.innerHTML = '';
  try{
    const r = await fetch('/library');
    if (!r.ok) throw new Error('Request failed (' + r.status + ').');
    libraryItems = await r.json();
    libraryLoaded = true;
    renderLibrary();
  }catch(e){
    errorBox.innerHTML = '<div class="error-card">' + esc(e.message || String(e)) + '</div>';
  }
}

document.getElementById('libSearch').addEventListener('input', renderLibrary);
document.getElementById('libTypeFilter').addEventListener('change', renderLibrary);
document.getElementById('libStatusFilter').addEventListener('change', renderLibrary);

async function viewLibraryItem(type, id){
  if (type === 'research'){
    switchTab('research');
    try{
      const r = await fetch('/research/' + id);
      if (!r.ok) throw new Error('Request failed (' + r.status + ').');
      renderResults(await r.json());
    }catch(e){
      showToast(e.message || String(e), 'error');
    }
  } else if (type === 'daily'){
    switchTab('daily-content');
    loadDailyContentDate(id);
  } else if (type === 'custom'){
    switchTab('custom-content');
    loadCustomContentRun(id);
  }
}
</script>
</body>
</html>
"""
