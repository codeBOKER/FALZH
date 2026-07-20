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
        active_settings = settings or (container.settings if container else get_settings())
        configure_logging(active_settings.log_level)
        app.state.container = container or await ServiceContainer.from_settings(active_settings)
        yield

    app = FastAPI(title="FALZH API", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    return app
