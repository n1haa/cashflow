from fastapi import FastAPI

from backend.api.transactions import router
from backend.api.forecast import router as forecast_router


def create_app() -> FastAPI:
    app = FastAPI(title="CashFlow AI", version="0.1.0")
    app.state.transactions = ()
    app.state.categorizations = {}
    app.include_router(router)
    app.include_router(forecast_router)
    return app


app = create_app()
