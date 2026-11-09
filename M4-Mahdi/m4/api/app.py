"""FastAPI application factory."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..config import get_settings
from ..data_integration.load_data import DataLoadError
from ..data_integration.validate_data import DataValidationError
from ..db.session import init_db
from ..modeling.demand import DemandPathwayError
from ..modeling.system import ScenarioDataError
from ..services import ServiceError
from .routers import admin, agent, auth, data, history, scenarios

logger = logging.getLogger("m4.api")

DESCRIPTION = """
Backend of **M4 — Agentic Energy Planning and Optimization for Tunisia**.

* Data integration of M1 demand forecasts, M3 benchmarks and the 2024 reference system (MOCK/REAL provenance on every result)
* Monthly energy simulation and Pyomo/HiGHS capacity-expansion optimization (single and multi-objective)
* Agentic planning workflow (LangGraph) with human approval and administrator review

All endpoints except `/api/health` and `/api/auth/*` require a Bearer access token.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    s = get_settings()
    logging.basicConfig(level=s.log_level.upper())
    app = FastAPI(title=s.app_name, version="1.0.0", description=DESCRIPTION, lifespan=lifespan,
                  docs_url="/docs", redoc_url="/redoc", openapi_url="/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origin_list, allow_credentials=True,
                       allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type"])

    @app.exception_handler(ServiceError)
    async def _service(_: Request, exc: ServiceError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.exception_handler(ScenarioDataError)
    @app.exception_handler(DemandPathwayError)
    async def _scenario(_: Request, exc: Exception):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(DataLoadError)
    @app.exception_handler(DataValidationError)
    async def _data(_: Request, exc: Exception):
        return JSONResponse(status_code=503, content={"detail": f"Input data problem: {exc}"})

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logger.exception("Unhandled error")
        return JSONResponse(status_code=500, content={"detail": "Internal server error. See server logs."})

    for r in (auth.router, data.router, scenarios.router, agent.router, admin.router, history.router):
        app.include_router(r)
    return app


app = create_app()
