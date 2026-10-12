"""Local development server: the narrator wiki with an optional fake logged-in user.

    NARWIKI_DEV_USER=alice uvicorn narwiki.devserver:app --port 8098      (from analysis/)

Never used in production (there the host application supplies the user)."""
import os

from fastapi import FastAPI

from .api import make_router

USER = os.environ.get("NARWIKI_DEV_USER")
app = FastAPI()
app.include_router(make_router(lambda req: dict(username=USER, is_admin=True) if USER else None))
