"""Local development server: the wizard with a fake logged-in user.

    ICM_DEV_USER=alice ICM_CACHE_DIR=/tmp/c uvicorn icm.devserver:app --port 8099
    ICM_CACHE_DIR=/tmp/c python3 -m icm worker          # in a second shell

Never used in production (there the host application supplies the user)."""
import os

from fastapi import FastAPI

from .api import make_router

USER = os.environ.get("ICM_DEV_USER")
app = FastAPI()
app.include_router(make_router(lambda req: dict(username=USER, is_admin=True) if USER else None))
