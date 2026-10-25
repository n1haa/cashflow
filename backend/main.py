from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from sqlalchemy.exc import SQLAlchemyError

from backend.api.transactions import router
from backend.api.forecast import router as forecast_router
from backend.api.planning import router as planning_router
from backend.api.auth import router as auth_router
from backend.auth import AuthRateLimiter, current_user, protect_mutation, require_user
from backend.config import Settings
from backend.database import make_engine, make_session_factory
from backend.models import User


class PublicAssets(StaticFiles):
    async def get_response(self, path, scope):
        if path not in {"styles.css", "auth.css", "app.js", "api.js", "presentation.js", "auth.js", "account.js"}:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        return await super().get_response(path, scope)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(title="CashFlow AI", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.workspaces = {}
    app.state.auth_limiter = AuthRateLimiter()
    app.include_router(auth_router)
    for protected_router in (router, forecast_router, planning_router):
        app.include_router(protected_router, dependencies=[Depends(require_user), Depends(protect_mutation)])
    frontend = Path(__file__).resolve().parents[1] / "frontend"

    @app.get("/assets/index.html", include_in_schema=False)
    @app.get("/dashboard", include_in_schema=False)
    @app.get("/", include_in_schema=False)
    def frontend_index(user: User | None = Depends(current_user)):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return FileResponse(frontend / "index.html")

    @app.get("/login", include_in_schema=False)
    @app.get("/register", include_in_schema=False)
    def auth_page(user: User | None = Depends(current_user)):
        if user is not None:
            return RedirectResponse("/dashboard", status_code=303)
        return FileResponse(frontend / "auth.html")

    app.mount("/assets", PublicAssets(directory=frontend), name="frontend-assets")

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError):
        return JSONResponse(status_code=503, content={"detail": "База данных временно недоступна. Повторите попытку позже."})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Never echo submitted passwords, including model-level validation input.
        messages = []
        for item in error.errors():
            message = item["msg"].removeprefix("Value error, ")
            if request.url.path.startswith("/api/auth/"):
                if item["type"] == "missing":
                    message = "Заполните все обязательные поля."
                elif item["type"] in {"string_type", "json_invalid", "model_attributes_type"}:
                    message = "Проверьте формат заполненных полей."
            messages.append({"loc": item["loc"], "msg": message, "type": item["type"]})
        return JSONResponse(status_code=422, content={"detail": messages})

    return app


app = create_app()
