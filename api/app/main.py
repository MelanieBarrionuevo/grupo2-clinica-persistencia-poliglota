"""API REST · Clínica ambulatoria (Grupo 2). Monolito modular en FastAPI.

Web → API REST → módulos de dominio → gestor responsable. El navegador nunca habla con
las bases de datos. Documentación OpenAPI en /docs.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import errores
from .db import pool
from .modulos import agenda, auth, busqueda, catalogo, citas, salud, sesiones

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def ciclo_vida(_: FastAPI):
    pool()  # abre el pool de PostgreSQL sin bloquear el arranque si aún no responde
    yield


app = FastAPI(
    lifespan=ciclo_vida,
    title="Clínica ambulatoria · API",
    version="1.0.0",
    description=(
        "Persistencia políglota: catálogo en MongoDB, agenda y citas en PostgreSQL, "
        "búsqueda en Typesense (proyección por outbox) y estado temporal en Redis. "
        "Usuarios de demostración: ver README."
    ),
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# Solo para el servidor de desarrollo de Vite; en Docker la web usa el proxy de nginx (mismo origen).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

errores.registrar(app)

PREFIJO = "/api/v1"
for modulo in (salud, auth, catalogo, busqueda, agenda, citas, sesiones):
    app.include_router(modulo.router, prefix=PREFIJO)

