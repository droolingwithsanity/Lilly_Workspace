from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from engine.orchestrator import Orchestrator
from api.routes import router

app = FastAPI(title="LillyOS Agile Platform")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(router)


@app.on_event("startup")
async def startup():
    orch = Orchestrator()
    orch.start()
    app.state.orchestrator = orch
