from __future__ import annotations

from fastapi import APIRouter

from . import approvals, disclosures, events, extensions, forms, push, tasks

router = APIRouter(prefix="/api/v1")
router.include_router(tasks.router)
router.include_router(forms.router)
router.include_router(approvals.router)
router.include_router(push.router)
router.include_router(disclosures.router)
router.include_router(extensions.router)
router.include_router(events.router)
