"""Formato uniforme de errores: {"error": {"codigo": "...", "mensaje": "..."}}.

También traduce la caída de un gestor en un 503 con un código claro, para que la web
muestre un mensaje entendible y el usuario sepa si puede reintentar.
"""
import logging

import psycopg
import psycopg_pool
import pymongo.errors
import redis.exceptions
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("clinica.errores")


class ApiError(Exception):
    def __init__(self, status: int, codigo: str, mensaje: str, **extra):
        super().__init__(mensaje)
        self.status = status
        self.codigo = codigo
        self.mensaje = mensaje
        self.extra = extra


class BusquedaNoDisponible(Exception):
    """Typesense no responde: la búsqueda queda degradada, el resto sigue operando."""


def _respuesta(status: int, codigo: str, mensaje: str, **extra) -> JSONResponse:
    cuerpo = {"error": {"codigo": codigo, "mensaje": mensaje, **extra}}
    return JSONResponse(status_code=status, content=cuerpo)


def registrar(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, exc: ApiError):
        return _respuesta(exc.status, exc.codigo, exc.mensaje, **exc.extra)

    @app.exception_handler(RequestValidationError)
    async def _validacion(_: Request, exc: RequestValidationError):
        detalles = [
            {"campo": ".".join(str(p) for p in e.get("loc", []) if p != "body"), "mensaje": e.get("msg")}
            for e in exc.errors()
        ]
        return _respuesta(422, "VALIDACION", "Datos inválidos en la solicitud", detalles=detalles)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        codigos = {401: "NO_AUTENTICADO", 403: "SIN_PERMISO", 404: "NO_ENCONTRADO", 405: "METODO_NO_PERMITIDO"}
        return _respuesta(exc.status_code, codigos.get(exc.status_code, "ERROR_HTTP"), str(exc.detail))

    @app.exception_handler(psycopg.OperationalError)
    @app.exception_handler(psycopg_pool.PoolTimeout)
    async def _postgres(_: Request, exc: Exception):
        log.warning("PostgreSQL no disponible: %s", exc)
        return _respuesta(503, "AGENDA_NO_DISPONIBLE",
                          "La agenda no está disponible en este momento. No se confirmó ningún cambio; puede reintentar.")

    @app.exception_handler(pymongo.errors.ConnectionFailure)
    @app.exception_handler(pymongo.errors.ServerSelectionTimeoutError)
    async def _mongo(_: Request, exc: Exception):
        log.warning("MongoDB no disponible: %s", exc)
        return _respuesta(503, "CATALOGO_NO_DISPONIBLE",
                          "El catálogo oficial no está disponible; no se puede validar la operación. Reintente luego.")

    @app.exception_handler(redis.exceptions.ConnectionError)
    @app.exception_handler(redis.exceptions.TimeoutError)
    async def _redis(_: Request, exc: Exception):
        log.warning("Redis no disponible: %s", exc)
        return _respuesta(503, "ESTADO_TEMPORAL_NO_DISPONIBLE",
                          "Los filtros y borradores temporales no están disponibles. Las citas no se ven afectadas.")

    @app.exception_handler(BusquedaNoDisponible)
    async def _busqueda(_: Request, exc: Exception):
        return _respuesta(503, "BUSQUEDA_NO_DISPONIBLE",
                          "El buscador no está disponible. Puede ver el catálogo oficial y reservar normalmente.")

    @app.exception_handler(Exception)
    async def _generico(_: Request, exc: Exception):
        log.exception("Error no controlado")
        return _respuesta(500, "ERROR_INTERNO", "Ocurrió un error inesperado. Intente nuevamente.")
