"""Salud de la API y del sincronizador. Nunca expone credenciales ni cadenas de conexión.

Política de /health (decisión Fase 4):
- 200 "ok"         → todos los gestores responden.
- 200 "degradado"  → cae Typesense o Redis: se puede reservar; la búsqueda o los borradores fallan.
- 503 "no_operativo" → cae PostgreSQL o MongoDB: no se pueden confirmar reservas con seguridad.
"""
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response

from ..db import busqueda, catalogo, pg, temporal
from ..errores import ApiError, BusquedaNoDisponible
from ..proyeccion import reindexar_todo, total_indexados
from ..seguridad import Usuario, requiere_rol

router = APIRouter(tags=["Salud y sincronizador"])
CRITICOS = ("postgresql", "mongodb")


def _medir(fn) -> dict:
    t0 = time.perf_counter()
    try:
        fn()
        return {"estado": "ok", "latencia_ms": round((time.perf_counter() - t0) * 1000, 1)}
    except Exception as exc:  # solo el tipo de error, sin detalles de conexión
        return {"estado": "caido", "error": type(exc).__name__}


def _typesense():
    if not busqueda().operations.is_healthy():
        raise RuntimeError("Typesense no sano")


def _pg():
    with pg() as conn:
        conn.execute("SELECT 1")


@router.get("/ping", include_in_schema=False)
def ping():
    return {"ok": True}


@router.get("/health")
def health(response: Response):
    dependencias = {
        "postgresql": _medir(_pg),
        "mongodb": _medir(lambda: catalogo().command("ping")),
        "typesense": _medir(_typesense),
        "redis": _medir(lambda: temporal().ping()),
    }
    caidos = [n for n, d in dependencias.items() if d["estado"] != "ok"]
    if any(n in CRITICOS for n in caidos):
        estado = "no_operativo"
        response.status_code = 503
    elif caidos:
        estado = "degradado"
    else:
        estado = "ok"
    return {"estado": estado, "api": "ok", "dependencias": dependencias,
            "verificado_en": datetime.now(timezone.utc)}


@router.get("/sync/estado")
def estado_sincronizador(_: Usuario = Depends(requiere_rol("ADMINISTRADOR", "RECEPCIONISTA"))):
    """Pantalla técnica: eventos de outbox pendientes/procesados, retraso y latido del worker."""
    db = catalogo()
    pendientes = db.outbox.count_documents({"procesado_en": None})
    con_error = db.outbox.count_documents({"procesado_en": None, "intentos": {"$gt": 0}})
    ultimos = []
    for e in db.outbox.find().sort("creado_en", -1).limit(15):
        retraso = None
        if e.get("procesado_en"):
            retraso = round((e["procesado_en"] - e["creado_en"]).total_seconds() * 1000)
        ultimos.append({
            "tipo": e["tipo"], "entidad_id": e["entidad_id"], "version": e["version"],
            "creado_en": e["creado_en"], "procesado_en": e.get("procesado_en"),
            "retraso_ms": retraso, "intentos": e.get("intentos", 0),
            "ultimo_error": (e.get("ultimo_error") or None) and e["ultimo_error"][:160],
        })
    procesados = [u["retraso_ms"] for u in ultimos if u["retraso_ms"] is not None]
    worker = db.sync_estado.find_one({"_id": "worker"}) or {}
    latido = worker.get("ultimo_latido")
    segundos_sin_latido = (datetime.now(timezone.utc) - latido).total_seconds() if latido else None
    try:
        indexados = total_indexados()
    except Exception:
        indexados = None
    return {
        "pendientes": pendientes,
        "pendientes_con_error": con_error,
        "retraso_promedio_ms": round(sum(procesados) / len(procesados)) if procesados else None,
        "documentos_en_busqueda": indexados,
        "worker": {
            "activo": segundos_sin_latido is not None and segundos_sin_latido < 15,
            "ultimo_latido": latido,
            "eventos_procesados": worker.get("eventos_procesados", 0),
            "ultima_reindexacion": worker.get("ultima_reindexacion"),
            "ultimo_error": worker.get("ultimo_error"),
            "ultimo_error_en": worker.get("ultimo_error_en"),
        },
        "ultimos_eventos": ultimos,
    }


@router.post("/sync/reindexar")
def reindexar(_: Usuario = Depends(requiere_rol("ADMINISTRADOR"))):
    """Reconstruye toda la proyección de búsqueda desde MongoDB (recuperación ante pérdida del índice)."""
    t0 = time.perf_counter()
    try:
        n = reindexar_todo()
    except ApiError:
        raise
    except Exception as exc:
        raise BusquedaNoDisponible() from exc
    catalogo().sync_estado.update_one(
        {"_id": "worker"}, {"$set": {"ultima_reindexacion": datetime.now(timezone.utc)}}, upsert=True
    )
    return {"reindexados": n, "duracion_ms": round((time.perf_counter() - t0) * 1000)}
