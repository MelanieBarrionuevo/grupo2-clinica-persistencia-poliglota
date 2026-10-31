"""PoC Fase 2 · Typesense para la Búsqueda (proyección del catálogo).

Demuestra:
  A. Búsqueda con error tipográfico ("cardiolgia" -> Cardiología).
  B. Filtros combinados por especialidad, sede y estado activo, con facetas.
  C. Orden por cercanía a un punto (coordenadas ficticias).
  D. Retraso medido entre un cambio (upsert) y su aparición en la búsqueda.
"""
import os
import time

import typesense
from typesense.exceptions import ObjectNotFound

cliente = typesense.Client({
    "nodes": [{
        "host": os.getenv("TYPESENSE_HOST", "localhost"),
        "port": os.getenv("TYPESENSE_PORT", "8108"),
        "protocol": "http",
    }],
    "api_key": os.getenv("TYPESENSE_API_KEY", "poc-typesense-key"),
    "connection_timeout_seconds": 5,
})

COLECCION = "profesionales_poc"
ESQUEMA = {
    "name": COLECCION,
    "fields": [
        {"name": "nombre", "type": "string"},
        {"name": "especialidades", "type": "string[]", "facet": True},
        {"name": "sedes", "type": "string[]", "facet": True},
        {"name": "idiomas", "type": "string[]", "facet": True, "optional": True},
        {"name": "modalidades", "type": "string[]", "facet": True},
        {"name": "servicios", "type": "string[]"},
        {"name": "activo", "type": "bool"},
        {"name": "ubicacion", "type": "geopoint"},
    ],
}

# Documentos aplanados (un documento por profesional; ubicación de su sede principal). Datos ficticios.
DOCUMENTOS = [
    {"id": "prof-001", "nombre": "Dra. Lucía Andrade", "especialidades": ["Cardiología"],
     "sedes": ["Norte"], "idiomas": ["es", "en"], "modalidades": ["PRESENCIAL"],
     "servicios": ["Consulta cardiológica", "Electrocardiograma"], "activo": True,
     "ubicacion": [-0.1500, -78.4800]},
    {"id": "prof-002", "nombre": "Dr. Martín Cevallos", "especialidades": ["Pediatría", "Medicina general"],
     "sedes": ["Norte", "Sur"], "modalidades": ["PRESENCIAL", "TELECONSULTA"],
     "servicios": ["Control de niño sano"], "activo": True, "ubicacion": [-0.2600, -78.5400]},
    {"id": "prof-003", "nombre": "Dr. Andrés Paredes", "especialidades": ["Cardiología"],
     "sedes": ["Sur"], "modalidades": ["PRESENCIAL"], "servicios": ["Consulta cardiológica"],
     "activo": True, "ubicacion": [-0.2700, -78.5500]},
    {"id": "prof-004", "nombre": "Dra. Elena Vaca", "especialidades": ["Dermatología"],
     "sedes": ["Centro"], "modalidades": ["TELECONSULTA"], "servicios": ["Consulta dermatológica"],
     "activo": False, "ubicacion": [-0.2200, -78.5100]},
]


def buscar(**params):
    base = {"q": "*", "query_by": "nombre,especialidades,servicios"}
    base.update(params)
    return cliente.collections[COLECCION].documents.search(base)


def main() -> None:
    try:
        cliente.collections[COLECCION].delete()
    except ObjectNotFound:
        pass
    cliente.collections.create(ESQUEMA)
    cliente.collections[COLECCION].documents.import_(DOCUMENTOS, {"action": "upsert"})

    # --- A. Tolerancia a errores ---
    r = buscar(q="cardiolgia", filter_by="activo:=true")
    print(f"A. 'cardiolgia' -> {[h['document']['nombre'] for h in r['hits']]} ({r['search_time_ms']} ms)")
    assert r["found"] == 2

    # --- B. Filtros + facetas ---
    r = buscar(q="*", filter_by="especialidades:=[Cardiología] && sedes:=[Sur] && activo:=true",
               facet_by="sedes,modalidades")
    print(f"B. Cardiología en sede Sur -> {[h['document']['nombre'] for h in r['hits']]}")
    assert [h["document"]["id"] for h in r["hits"]] == ["prof-003"]

    # --- C. Cercanía (paciente ficticio en el norte) ---
    r = buscar(q="*", filter_by="activo:=true", sort_by="ubicacion(-0.1400, -78.4750):asc")
    print(f"C. Más cercanos primero -> {[h['document']['nombre'] for h in r['hits']]}")
    assert r["hits"][0]["document"]["id"] == "prof-001"

    # --- D. Retraso de actualización (lo que hará el sincronizador) ---
    t0 = time.perf_counter()
    cliente.collections[COLECCION].documents.upsert({**DOCUMENTOS[0], "sedes": ["Sur"]})
    while True:
        r = buscar(q="*", filter_by="sedes:=[Sur] && id:=prof-001")
        if r["found"] == 1:
            break
        time.sleep(0.01)
    retraso_ms = (time.perf_counter() - t0) * 1000
    print(f"D. Cambio de sede visible en la búsqueda tras {retraso_ms:.1f} ms (sin contar el sincronizador)")

    print("\nOK · Typesense cubre texto con errores, filtros, facetas y cercanía")


if __name__ == "__main__":
    main()
