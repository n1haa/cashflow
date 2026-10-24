from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from backend.api.transactions import router
from backend.api.forecast import router as forecast_router


def create_app() -> FastAPI:
    app = FastAPI(title="CashFlow AI", version="0.1.0")
    app.state.transactions = ()
    app.state.categorizations = {}
    app.include_router(router)
    app.include_router(forecast_router)
    frontend = Path(__file__).resolve().parents[1] / "frontend"
    app.mount("/assets", StaticFiles(directory=frontend), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    def frontend_index():
        return FileResponse(frontend / "index.html")

    return app


app = create_app()
