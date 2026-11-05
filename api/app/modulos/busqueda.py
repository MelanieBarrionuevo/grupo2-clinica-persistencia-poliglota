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
    cerca: str | None = Query(
        default=None, pattern=r"^-?\d{1,2}(\.\d+)?,-?\d{1,3}(\.\d+)?$",
        description="Ordena por cercanía a este punto: 'latitud,longitud' (coordenadas ficticias)",
    ),
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
    if cerca:
        lat, lon = (float(x) for x in cerca.split(","))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ApiError(422, "FILTRO_INVALIDO", "Coordenadas fuera de rango")
        # La sede más cercana del profesional decide su posición; luego relevancia y nombre.
        params["sort_by"] = f"ubicaciones({lat}, {lon}):asc,_text_match:desc,nombre_orden:asc"
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
            "distancia_km": (round(hit["geo_distance_meters"]["ubicaciones"] / 1000, 1)
                             if cerca and "ubicaciones" in hit.get("geo_distance_meters", {}) else None),
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
        "ordenado_por": "cercania" if cerca else ordenar,
        "tiempo_ms": r.get("search_time_ms"),
    }
