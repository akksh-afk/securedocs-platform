"""HTTP API and officer console for the AI-based fake identity & document screening system."""
from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import logging

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from src.registry import LIST_TYPES
from src.screening import ScreeningService

MAX_BYTES = 12 * 1024 * 1024
STATIC = Path(__file__).parent / 'static'

service = ScreeningService()
# Screening runs off the event loop; one at a time per process because OpenCV face detectors keep
# per-call state. Scale throughput with more server processes (uvicorn --workers N).
_screen_lock = threading.Lock()


def _screen_locked(*args, **kwargs):
    with _screen_lock:
        return service.screen(*args, **kwargs)


log = logging.getLogger('screening')


def _warm_up():
    # Load neural networks before the first traveller is screened.
    from src import face_verification, mrz_reader, tampering
    err = mrz_reader.model_error()
    if err:
        log.error('MRZ reader unavailable, screening requests will be refused: %s', err)
    tampering.pixel_model_available()
    if face_verification.models_available():
        face_verification._detector(); face_verification._recognizer()


@asynccontextmanager
async def lifespan(_app):
    threading.Thread(target=_warm_up, daemon=True).start()
    yield


app = FastAPI(title='AI Fake Identity & Document Screening System', version='2.0.0', lifespan=lifespan,
              description='SIH 26188 - OCR, document validation, tampering detection, face verification and risk scoring.')


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    log.exception('Unhandled error on %s', request.url.path)
    return JSONResponse(status_code=500, content=dict(detail=f'Internal error: {type(exc).__name__}: {exc}'))


def _require_models():
    from src.mrz_reader import model_error
    err = model_error()
    if err:
        raise HTTPException(503, f'Screening unavailable: {err}')


async def _read_image(upload: UploadFile | None, name: str) -> bytes | None:
    if upload is None or not upload.filename:
        return None
    if upload.content_type and not upload.content_type.startswith('image/'):
        raise HTTPException(415, f'{name}: only image uploads are supported')
    data = await upload.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f'{name}: image exceeds the 12 MB limit')
    if not data:
        raise HTTPException(400, f'{name}: empty upload')
    return data


@app.get('/', include_in_schema=False)
def console():
    return FileResponse(STATIC / 'index.html')


@app.get('/health')
def health():
    return dict(status='ok', **service.status())


@app.post('/api/v1/screen')
async def screen(document: UploadFile = File(..., description='Passport, visa, ID card, driving licence or permit image'),
                 live_photo: UploadFile | None = File(None, description='Live capture of the traveller for face verification'),
                 companion: UploadFile | None = File(None, description='Second document presented together, e.g. the visa'),
                 document_type: str | None = Form(None), companion_type: str | None = Form(None),
                 checkpoint_id: str | None = Form(None), officer_id: str | None = Form(None),
                 entry_date: date | None = Form(None), intended_stay_days: int | None = Form(None),
                 include_images: bool = Form(False)):
    _require_models()
    doc = await _read_image(document, 'document')
    live = await _read_image(live_photo, 'live_photo')
    comp = await _read_image(companion, 'companion')
    context = dict(checkpoint_id=checkpoint_id, officer_id=officer_id, entry_date=entry_date.isoformat() if entry_date else None,
                   intended_stay_days=intended_stay_days)
    try:
        return await run_in_threadpool(_screen_locked, doc, live_photo=live, companion=comp, document_type=document_type or None,
                                       companion_type=companion_type or None, context=context, include_images=include_images)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post('/screen', include_in_schema=False)
async def screen_legacy(file: UploadFile = File(...)):
    """Backwards-compatible single-image endpoint from the original build."""
    _require_models()
    data = await _read_image(file, 'file')
    try:
        return await run_in_threadpool(_screen_locked, data)
    except ValueError as e:
        raise HTTPException(422, str(e))


class WatchEntry(BaseModel):
    list_type: str = Field(..., description=f'One of {LIST_TYPES}')
    reason: str
    added_by: str = 'api'
    document_number: str | None = None
    document_type: str | None = None
    issuing_state: str | None = None
    surname: str | None = None
    given_names: str | None = None
    date_of_birth: str | None = Field(None, description='ISO date YYYY-MM-DD')
    nationality: str | None = None


@app.get('/api/v1/watchlist')
def list_watchlist():
    return service.registry.list_watch()


@app.post('/api/v1/watchlist', status_code=201)
def add_watchlist(entry: WatchEntry):
    data = entry.model_dump()
    try:
        wid = service.registry.add_watch(data.pop('list_type'), data.pop('reason'), data.pop('added_by'), **data)
    except ValueError as e:
        raise HTTPException(422, str(e))
    service.audit.append(dict(event='watchlist_add', entry_id=wid, list_type=entry.list_type, added_by=entry.added_by))
    return dict(id=wid)


@app.delete('/api/v1/watchlist/{entry_id}')
def remove_watchlist(entry_id: str):
    if not service.registry.deactivate_watch(entry_id):
        raise HTTPException(404, 'watchlist entry not found')
    service.audit.append(dict(event='watchlist_deactivate', entry_id=entry_id))
    return dict(deactivated=entry_id)


@app.get('/api/v1/audit')
def audit_log(limit: int = 50, screening_id: str | None = None):
    return service.audit.read(limit=min(limit, 500), screening_id=screening_id)


@app.get('/api/v1/audit/verify')
def audit_verify():
    return service.audit.verify()
