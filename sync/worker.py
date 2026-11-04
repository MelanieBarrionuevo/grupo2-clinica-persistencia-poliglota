"""Sincronizador catálogo (MongoDB outbox) → búsqueda (Typesense).

Ciclo:
 1. Latido en MongoDB (`sync_estado`) para la pantalla técnica.
 2. Si la colección de Typesense no existe (índice perdido) → reindexación completa desde MongoDB.
 3. Lee eventos pendientes del outbox en orden de creación.
 4. Por cada evento reconstruye desde la FUENTE OFICIAL los documentos afectados y hace upsert/delete.
 5. Solo si Typesense respondió OK marca el evento como procesado (procesado_en, retraso_ms).
 6. Ante error: incrementa `intentos`, guarda `ultimo_error` y reprograma con backoff exponencial (máx. 60 s).

Garantía: entrega al-menos-una-vez + aplicación idempotente (se lee siempre el estado actual).
Uso:
    python worker.py              # bucle continuo
    python worker.py --reindexar  # reconstrucción completa y salida
"""
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from pymongo import ASCENDING

from app.config import config
from app.db import catalogo
from app.proyeccion import aplicar_evento, asegurar_coleccion, reindexar_todo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s sync: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("sync")

LOTE = 50
BACKOFF_MAX_S = 60


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def latido(**extra) -> None:
    catalogo().sync_estado.update_one(
        {"_id": "worker"}, {"$set": {"ultimo_latido": ahora(), **extra}}, upsert=True
    )


def procesar_lote() -> int:
    db = catalogo()
    momento = ahora()
    pendientes = list(
        db.outbox.find({
            "procesado_en": None,
            "$or": [{"proximo_intento_en": {"$exists": False}}, {"proximo_intento_en": {"$lte": momento}}],
        }).sort([("creado_en", ASCENDING)]).limit(LOTE)
    )
    procesados = 0
    for ev in pendientes:
        try:
            n = aplicar_evento(ev)
        except Exception as exc:
            intentos = int(ev.get("intentos", 0)) + 1
            espera = min(2 ** intentos, BACKOFF_MAX_S)
            db.outbox.update_one(
                {"_id": ev["_id"]},
                {"$set": {"intentos": intentos, "ultimo_error": f"{type(exc).__name__}: {str(exc)[:200]}",
                          "proximo_intento_en": ahora() + timedelta(seconds=espera)}},
            )
            log.warning("Evento %s %s falló (intento %s), reintento en %ss: %s",
                        ev["tipo"], ev["entidad_id"], intentos, espera, type(exc).__name__)
            # Si Typesense está caído, no tiene sentido seguir con el resto del lote.
            break
        fin = ahora()
        db.outbox.update_one(
            {"_id": ev["_id"]},
            {"$set": {"procesado_en": fin, "ultimo_error": None,
                      "retraso_ms": int((fin - ev["creado_en"]).total_seconds() * 1000),
                      "documentos_afectados": n}},
        )
        procesados += 1
        log.info("Sincronizado %s %s v%s (%s documentos)", ev["tipo"], ev["entidad_id"], ev["version"], n)
    if procesados:
        db.sync_estado.update_one({"_id": "worker"}, {"$inc": {"eventos_procesados": procesados}}, upsert=True)
    return procesados


def bucle() -> None:
    intervalo = config.sync_intervalo_ms / 1000
    log.info("Sincronizador iniciado (intervalo %.1fs)", intervalo)
    while True:
        try:
            latido()
            # Comprobación barata en cada ciclo: si Typesense perdió su índice (volumen borrado),
            # se recrea y se reconstruye completo desde la fuente oficial.
            if asegurar_coleccion():
                n = reindexar_todo()
                latido(ultima_reindexacion=ahora())
                log.info("Índice reconstruido desde MongoDB: %s documentos", n)
            procesar_lote()
        except Exception as exc:
            # MongoDB o Typesense caídos: los eventos siguen pendientes y se reintenta en el próximo ciclo.
            log.warning("Ciclo con error (%s); se reintentará", type(exc).__name__)
            time.sleep(min(5, intervalo * 5))
        time.sleep(intervalo)


if __name__ == "__main__":
    if "--reindexar" in sys.argv:
        print(f"Reindexados: {reindexar_todo()}")
        latido(ultima_reindexacion=ahora())
    else:
        bucle()
