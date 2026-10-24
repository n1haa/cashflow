from fastapi import FastAPI

from backend.api.transactions import router


def create_app() -> FastAPI:
    app = FastAPI(title="CashFlow AI", version="0.1.0")
    app.state.transactions = ()
    app.state.categorizations = {}
    app.include_router(router)
    return app


app = create_app()
