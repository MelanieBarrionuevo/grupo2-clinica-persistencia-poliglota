"""Módulo Búsqueda · Typesense (proyección derivada del catálogo, consistencia eventual).

Nunca devuelve disponibilidad: los cupos se leen siempre de PostgreSQL (RN-02).
Si Typesense cae responde 503 BUSQUEDA_NO_DISPONIBLE y la web pasa a modo degradado
(listado directo de MongoDB vía GET /profesionales).
"""
from typing import Literal

from fastapi import APIRouter, Query

from ..db import busqueda
from ..errores import ApiError, BusquedaNoDisponible
from ..proyeccion import COLECCION

router = APIRouter(tags=["Búsqueda (Typesense)"])


def _valor_filtro(v: str) -> str:
    if "`" in v:
        raise ApiError(422, "FILTRO_INVALIDO", "Valor de filtro inválido")
    return f"`{v}`"


@router.get("/buscar")
def buscar(
    q: str = Query(default="", max_length=80, description="Texto libre (tolera errores de tipeo)"),
    especialidad_id: str | None = None,
    sede_id: str | None = None,
    modalidad: Literal["PRESENCIAL", "TELECONSULTA"] | None = None,
    idioma: str | None = Query(default=None, max_length=10),
    ordenar: Literal["relevancia", "nombre"] = "relevancia",
    pagina: int = Query(default=1, ge=1, le=100),
    por_pagina: int = Query(default=12, ge=1, le=50),
):
    filtros = []
    if especialidad_id:
        filtros.append(f"especialidad_ids:={_valor_filtro(especialidad_id)}")
    if sede_id:
        filtros.append(f"sede_ids:={_valor_filtro(sede_id)}")
    if modalidad:
        filtros.append(f"modalidades:={_valor_filtro(modalidad)}")
    if idioma:
        filtros.append(f"idiomas:={_valor_filtro(idioma)}")

    params = {
        "q": q.strip() or "*",
        "query_by": "nombre,especialidades,servicios,sedes",
        "num_typos": 2,
        "facet_by": "especialidades,sedes,modalidades",
        "page": pagina,
        "per_page": por_pagina,
        "sort_by": "nombre_orden:asc" if ordenar == "nombre" or not q.strip() else "_text_match:desc,nombre_orden:asc",
    }
    if filtros:
        params["filter_by"] = " && ".join(filtros)

    try:
        r = busqueda().collections[COLECCION].documents.search(params)
    except ApiError:
        raise
    except Exception as exc:  # Typesense caído, sin colección o timeout
        raise BusquedaNoDisponible() from exc

    resultados = []
    for hit in r.get("hits", []):
        d = hit["document"]
        resultados.append({
            "profesional_id": d["id"],
            "nombre": d["nombre"],
            "especialidades": d.get("especialidades", []),
            "sedes": d.get("sedes", []),
            "modalidades": d.get("modalidades", []),
            "idiomas": d.get("idiomas", []),
            "servicios": d.get("servicios", []),
            "version": d.get("version"),
            "sincronizado_en": d.get("sincronizado_en"),
        })
    facetas = {
        f["field_name"]: [{"valor": c["value"], "conteo": c["count"]} for c in f.get("counts", [])]
        for f in r.get("facet_counts", [])
    }
    return {
        "total": r.get("found", 0),
        "pagina": pagina,
        "resultados": resultados,
        "facetas": facetas,
        "origen": "typesense",
        "tiempo_ms": r.get("search_time_ms"),
    }
