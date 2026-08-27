from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import auth, db
from .brand_assets import (
    ALLOWED_CONTENT_TYPES,
    MAX_UPLOAD_BYTES,
    asset_file_path,
    delete_asset_file,
    save_asset_file,
)
from .config import Settings, get_settings

router = APIRouter(prefix="/assets", tags=["assets"])


def _asset_payload(row) -> dict:
    return {
        "id": row["id"],
        "filename": row["filename"],
        "content_type": row["content_type"],
        "is_logo": bool(row["is_logo"]),
        "created_at": row["created_at"],
        "url": f"/assets/{row['id']}/file",
    }


@router.get("")
def list_assets(current: auth.CurrentUser = Depends(auth.get_current_user)) -> list[dict]:
    settings = get_settings()
    rows = db.list_brand_assets(settings.database_path, current.company_id)
    return [_asset_payload(row) for row in rows]


@router.post("/upload")
async def upload_asset(
    file: UploadFile,
    is_logo: bool = False,
    current: auth.CurrentUser = Depends(auth.require_owner),
) -> dict:
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {file.content_type!r} - allowed: {sorted(ALLOWED_CONTENT_TYPES)}.",
        )
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail=f"File too large - max {MAX_UPLOAD_BYTES} bytes.")

    settings = get_settings()
    filename = file.filename or "asset"
    asset_id = db.insert_brand_asset(
        settings.database_path,
        company_id=current.company_id,
        filename=filename,
        content_type=file.content_type,
        is_logo=is_logo,
    )
    save_asset_file(settings, current.company_slug, asset_id, filename, content)
    row = db.get_brand_asset(settings.database_path, current.company_id, asset_id)
    return _asset_payload(row)


@router.get("/{asset_id}/file")
def get_asset_file(
    asset_id: int,
    current: auth.CurrentUser = Depends(auth.get_current_user),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    row = db.get_brand_asset(settings.database_path, current.company_id, asset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such asset.")
    path = asset_file_path(settings, current.company_slug, asset_id, row["filename"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Asset file is missing on disk.")
    return FileResponse(path, media_type=row["content_type"])


@router.delete("/{asset_id}")
def delete_asset(
    asset_id: int,
    current: auth.CurrentUser = Depends(auth.require_owner),
    settings: Settings = Depends(get_settings),
) -> dict:
    row = db.get_brand_asset(settings.database_path, current.company_id, asset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such asset.")
    delete_asset_file(settings, current.company_slug, asset_id, row["filename"])
    db.delete_brand_asset(settings.database_path, current.company_id, asset_id)
    return {"ok": True}


__all__ = ["router"]
