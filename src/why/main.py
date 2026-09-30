import logging

from fastapi import FastAPI

from why import __version__
from why.api import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="Why?",
    version=__version__,
    description="Answers 'why is the code this way?' from a repository's history.",
)
app.include_router(router)
