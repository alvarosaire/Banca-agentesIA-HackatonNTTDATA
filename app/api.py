"""API JSON: lanzar casos, consultar trazas y resolver aprobaciones."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agents.engine import engine
from app.agents.trace import store
from sectors import active

router = APIRouter(prefix="/api")


class RunRequest(BaseModel):
    input: str | None = None  # vacío -> caso de ejemplo
    case: str = "demo"


class DecisionRequest(BaseModel):
    approved: bool
    by: str = "supervisor"
    comment: str | None = None


@router.get("/ui")
def ui_config() -> dict:
    """Textos de la interfaz (sectors/ui_base.json + sectors/<SECTOR>/ui.json) + caso de ejemplo."""
    pack = active()
    return {"ui": pack.ui, "example": pack.example, "sector": pack.name}


@router.get("/example")
def example() -> dict:
    return {"input": active().example}


@router.post("/runs", status_code=202)
def create_run(req: RunRequest) -> dict:
    pack = active()
    text = (req.input or "").strip() or pack.example
    run = engine.start(pack.flow, text, case=req.case, background=True)
    return {"id": run.id, "status": run.status}


@router.get("/runs")
def list_runs() -> list[dict]:
    return [{"id": r.id, "case": r.case, "status": r.status, "created_at": r.created_at} for r in store.list()]


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = store.get(run_id)
    if not run:
        raise HTTPException(404, "Run no encontrado")
    return run.to_dict()


@router.post("/approvals/{approval_id}")
def decide(approval_id: str, req: DecisionRequest) -> dict:
    try:
        run = engine.decide(approval_id, req.approved, by=req.by, comment=req.comment, background=True)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"run_id": run.id, "status": run.status}
