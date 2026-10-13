from fastapi import FastAPI

from app.config import get_settings


def create_app() -> FastAPI:
    application = FastAPI(title=get_settings().app_name, version="0.1.0")

    @application.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        """Liveness probe: dependency health is checked by Docker Compose."""
        return {"status": "ok"}

    return application


app = create_app()
