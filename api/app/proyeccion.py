"""Proyección de búsqueda: documento derivado del catálogo oficial (MongoDB) → Typesense.

La usa el sincronizador (sync/worker.py) y el endpoint de reindexación. Siempre reconstruye
el documento leyendo el estado ACTUAL de MongoDB, por eso aplicar un evento viejo o repetido
es inocuo (idempotente): nunca se escribe en Typesense un dato que no esté en la fuente.
"""
import logging
import time

import typesense.exceptions as tse

from .db import busqueda, catalogo

log = logging.getLogger("clinica.proyeccion")

COLECCION = "profesionales"

ESQUEMA = {
    "name": COLECCION,
    "fields": [
        {"name": "nombre", "type": "string"},
        {"name": "nombre_orden", "type": "string", "sort": True},
        {"name": "especialidades", "type": "string[]", "facet": True},
        {"name": "especialidad_ids", "type": "string[]", "facet": True},
        {"name": "sedes", "type": "string[]", "facet": True},
        {"name": "sede_ids", "type": "string[]", "facet": True},
        {"name": "idiomas", "type": "string[]", "facet": True, "optional": True},
        {"name": "modalidades", "type": "string[]", "facet": True},
        {"name": "servicios", "type": "string[]", "optional": True},
        {"name": "version", "type": "int32"},
        {"name": "sincronizado_en", "type": "int64"},
    ],
}


def asegurar_coleccion() -> bool:
    """Crea la colección si no existe. Devuelve True si la tuvo que crear (índice vacío)."""
    cliente = busqueda()
    try:
        cliente.collections[COLECCION].retrieve()
        return False
    except tse.ObjectNotFound:
        cliente.collections.create(ESQUEMA)
        log.info("Colección Typesense '%s' creada", COLECCION)
        return True


def construir_documento(prof: dict) -> dict:
    """Desnormaliza nombres de especialidades, sedes y servicios activos (lectura rápida)."""
    db = catalogo()
    esp_ids = prof.get("especialidad_ids", [])
    sede_ids = [s["sede_id"] for s in prof.get("sedes", [])]
    especialidades = {d["_id"]: d["nombre"] for d in db.especialidades.find({"_id": {"$in": esp_ids}, "activo": True})}
    sedes = {d["_id"]: d["nombre"] for d in db.sedes.find({"_id": {"$in": sede_ids}, "activo": True})}
    servicios = [d["nombre"] for d in db.servicios.find({"especialidad_id": {"$in": esp_ids}, "activo": True})]
    return {
        "id": prof["_id"],
        "nombre": prof["nombre"],
        "nombre_orden": prof["nombre"].lower(),
        "especialidades": [especialidades[e] for e in esp_ids if e in especialidades],
        "especialidad_ids": [e for e in esp_ids if e in especialidades],
        "sedes": [sedes[s] for s in sede_ids if s in sedes],
        "sede_ids": [s for s in sede_ids if s in sedes],
        "idiomas": prof.get("idiomas", []),
        "modalidades": prof.get("modalidades", []),
        "servicios": sorted(set(servicios)),
        "version": int(prof.get("version", 1)),
        "sincronizado_en": int(time.time() * 1000),
    }


def sincronizar_profesional(profesional_id: str) -> str:
    """Upsert si está activo en la fuente oficial; eliminación si se desactivó o no existe."""
    prof = catalogo().profesionales.find_one({"_id": profesional_id})
    docs = busqueda().collections[COLECCION].documents
    if not prof or not prof.get("activo", False):
        try:
            docs[profesional_id].delete()
        except tse.ObjectNotFound:
            pass
        return "eliminado"
    docs.upsert(construir_documento(prof))
    return "upsert"


def profesionales_afectados(entidad_tipo: str, entidad_id: str) -> list[str]:
    """Qué documentos de búsqueda dependen de la entidad modificada."""
    db = catalogo()
    if entidad_tipo == "profesional":
        return [entidad_id]
    if entidad_tipo == "especialidad":
        filtro = {"especialidad_ids": entidad_id}
    elif entidad_tipo == "sede":
        filtro = {"sedes.sede_id": entidad_id}
    elif entidad_tipo == "servicio":
        serv = db.servicios.find_one({"_id": entidad_id}, {"especialidad_id": 1})
        if not serv:
            return []
        filtro = {"especialidad_ids": serv["especialidad_id"]}
    else:
        return []
    return [d["_id"] for d in db.profesionales.find(filtro, {"_id": 1})]


def aplicar_evento(evento: dict) -> int:
    ids = profesionales_afectados(evento["entidad_tipo"], evento["entidad_id"])
    for pid in ids:
        sincronizar_profesional(pid)
    return len(ids)


def reindexar_todo() -> int:
    """Reconstrucción completa desde MongoDB (si el índice se pierde o queda inconsistente)."""
    cliente = busqueda()
    try:
        cliente.collections[COLECCION].delete()
    except tse.ObjectNotFound:
        pass
    cliente.collections.create(ESQUEMA)
    docs = [construir_documento(p) for p in catalogo().profesionales.find({"activo": True})]
    if docs:
        resultado = cliente.collections[COLECCION].documents.import_(docs, {"action": "upsert"})
        errores = [r for r in resultado if not r.get("success")]
        if errores:
            raise RuntimeError(f"Errores al importar en Typesense: {errores[:3]}")
    log.info("Reindexación completa: %s profesionales", len(docs))
    return len(docs)


def total_indexados() -> int:
    return int(busqueda().collections[COLECCION].retrieve().get("num_documents", 0))
