from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import Settings, get_settings
from app.services.container import ServiceContainer
from app.utils.logging import configure_logging


def create_app(
    *,
    settings: Settings | None = None,
    container: ServiceContainer | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = None
        app.state.container_init_error = None

        if container is not None:
            active_settings = container.settings
            app.state.container = container
        else:
            try:
                active_settings = settings or get_settings()
            except Exception as exc:  # pragma: no cover - defensive startup path
                active_settings = None
                app.state.container_init_error = str(exc)

        if active_settings is not None:
            configure_logging(active_settings.log_level)
        else:
            configure_logging("INFO")

        if container is None and active_settings is not None:
            try:
                app.state.container = await ServiceContainer.from_settings(active_settings)
            except Exception as exc:  # pragma: no cover - defensive startup path
                app.state.container = None
                app.state.container_init_error = str(exc)

        yield

    app = FastAPI(title="FALZH API", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    return app
