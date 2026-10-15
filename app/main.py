from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import auth, jobs, resumes, search
from app.config import get_settings


def create_app() -> FastAPI:
    application = FastAPI(title=get_settings().app_name, version="0.1.0")

    application.include_router(auth.router)
    application.include_router(resumes.router)
    application.include_router(jobs.router)
    application.include_router(search.router)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = []
        for error in exc.errors():
            details.append({"loc": error["loc"], "msg": error["msg"], "type": error["type"]})
        return JSONResponse(status_code=422, content={"detail": details})

    @application.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        """Liveness probe: dependency health is checked by Docker Compose."""
        return {"status": "ok"}

    return application


app = create_app()
