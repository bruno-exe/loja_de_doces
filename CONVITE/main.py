from fastapi import FastAPI
from routes import router

app = FastAPI(title="Convite Kennedy")

app.include_router(router)
