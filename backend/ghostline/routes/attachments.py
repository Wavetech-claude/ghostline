"""A confirmation screenshot or PDF, stored with its trade in the private bucket and read back through the API."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response

from ghostline.auth import OwnerSub

router = APIRouter()
ALLOWED = {"image/png", "image/jpeg", "image/heic", "application/pdf"}
MAX_BYTES = 15 * 1024 * 1024


@router.post("/attachments", status_code=201)
async def upload(request: Request, trade_id: int, file: Annotated[UploadFile, File()], sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    if not db.fetch_one("SELECT 1 AS ok FROM trades WHERE id = %s", (trade_id,)):
        raise HTTPException(status_code=404, detail="no such trade")
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED:
        raise HTTPException(status_code=415, detail=f"not a screenshot or PDF: {content_type or 'unknown type'}")
    data = await file.read(MAX_BYTES + 1)
    if not data:
        raise HTTPException(status_code=422, detail="empty file")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="larger than 15 MB")
    key = f"attachments/{trade_id}/{uuid.uuid4().hex}"
    request.app.state.storage.put(key, data, content_type)
    row = db.fetch_one("INSERT INTO attachments (trade_id, object_key, content_type, bytes, filename) "
                       "VALUES (%s, %s, %s, %s, %s) RETURNING id, trade_id, content_type, bytes, filename, created_at",
                       (trade_id, key, content_type, len(data), (file.filename or "")[:200] or None))
    with db.connection() as conn:
        conn.commit()
    return {**row, "created_at": row["created_at"].isoformat()}


@router.get("/attachments/{attachment_id}")
def download(attachment_id: int, request: Request, sub: str = OwnerSub) -> Response:
    db = request.app.state.db
    row = db.fetch_one("SELECT object_key, content_type, filename FROM attachments WHERE id = %s", (attachment_id,))
    if not row:
        raise HTTPException(status_code=404, detail="no such attachment")
    try:
        data, content_type = request.app.state.storage.get(row["object_key"])
    except KeyError:
        raise HTTPException(status_code=404, detail="the file is missing from storage") from None
    headers = {"Content-Disposition": f'inline; filename="{row.get("filename") or attachment_id}"'}
    return Response(content=data, media_type=content_type or row["content_type"], headers=headers)


@router.delete("/attachments/{attachment_id}")
def remove(attachment_id: int, request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    row = db.fetch_one("SELECT object_key FROM attachments WHERE id = %s", (attachment_id,))
    if not row:
        raise HTTPException(status_code=404, detail="no such attachment")
    request.app.state.storage.delete(row["object_key"])
    db.execute("DELETE FROM attachments WHERE id = %s", (attachment_id,))
    return {"ok": True}
