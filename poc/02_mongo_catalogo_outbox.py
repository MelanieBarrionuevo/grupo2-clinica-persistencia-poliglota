"""PoC Fase 2 · MongoDB para el Catálogo.

Demuestra:
  A. Fichas de profesionales con atributos distintos en la misma colección.
  B. El validador $jsonSchema rechaza una ficha sin los campos mínimos.
  C. Profesional + evento de outbox en UNA transacción (requiere replica set).
  D. Si la transacción aborta, no queda ni el profesional ni el evento.
"""
import os
from datetime import datetime, timezone

from pymongo import ASCENDING, MongoClient
from pymongo.errors import WriteError

URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/?replicaSet=rs0&directConnection=true")

VALIDADOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "nombre", "especialidad_ids", "activo"],
        "properties": {
            "nombre": {"bsonType": "string", "minLength": 3},
            "especialidad_ids": {"bsonType": "array", "minItems": 1},
            "activo": {"bsonType": "bool"},
            "idiomas": {"bsonType": "array"},
            "modalidades": {"bsonType": "array", "items": {"enum": ["PRESENCIAL", "TELECONSULTA"]}},
        },
    }
}


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def main() -> None:
    cliente = MongoClient(URI, serverSelectionTimeoutMS=5000)
    db = cliente["catalogo_poc"]
    cliente.drop_database("catalogo_poc")
    db.create_collection("profesionales", validator=VALIDADOR)
    db.create_collection("outbox")
    db.outbox.create_index([("procesado_en", ASCENDING), ("creado_en", ASCENDING)])

    # --- A. Atributos variables ---
    db.profesionales.insert_many([
        {
            "_id": "prof-001", "nombre": "Dra. Lucía Andrade", "especialidad_ids": ["esp-cardio"],
            "activo": True, "idiomas": ["es", "en"], "modalidades": ["PRESENCIAL"],
            "sedes": [{"sede_id": "sede-norte", "horario_ref": "L-V 08:00-13:00"}],
        },
        {
            "_id": "prof-002", "nombre": "Dr. Martín Cevallos", "especialidad_ids": ["esp-pedia", "esp-general"],
            "activo": True, "modalidades": ["PRESENCIAL", "TELECONSULTA"],
            "sedes": [
                {"sede_id": "sede-norte", "horario_ref": "L-M 14:00-18:00"},
                {"sede_id": "sede-sur", "horario_ref": "J-V 08:00-12:00"},
            ],
            "certificaciones": ["Neonatología básica"],  # atributo que el otro no tiene
        },
    ])
    print("A. Fichas heterogéneas guardadas:", [sorted(d.keys()) for d in db.profesionales.find()])

    # --- B. Validación ---
    try:
        db.profesionales.insert_one({"_id": "prof-mal", "activo": True})
        print("B. ERROR: se aceptó una ficha inválida")
        raise SystemExit(1)
    except WriteError:
        print("B. Ficha sin nombre ni especialidad -> rechazada por $jsonSchema")

    # --- C. Transacción profesional + outbox ---
    with cliente.start_session() as sesion:
        def cambio(s):
            db.profesionales.update_one(
                {"_id": "prof-001"}, {"$set": {"sedes.0.sede_id": "sede-sur"}}, session=s
            )
            db.outbox.insert_one(
                {"tipo": "profesional.actualizado", "entidad_id": "prof-001",
                 "creado_en": ahora(), "procesado_en": None},
                session=s,
            )
        sesion.with_transaction(cambio)
    pendientes = db.outbox.count_documents({"procesado_en": None})
    print(f"C. Cambio de sede + evento outbox confirmados juntos -> eventos pendientes: {pendientes}")
    assert pendientes == 1

    # --- D. Aborto ---
    try:
        with cliente.start_session() as sesion:
            def cambio_fallido(s):
                db.profesionales.insert_one(
                    {"_id": "prof-003", "nombre": "Dra. Sofía Ruiz", "especialidad_ids": ["esp-derma"],
                     "activo": True}, session=s,
                )
                db.outbox.insert_one(
                    {"tipo": "profesional.creado", "entidad_id": "prof-003",
                     "creado_en": ahora(), "procesado_en": None}, session=s,
                )
                raise RuntimeError("fallo simulado antes del commit")
            sesion.with_transaction(cambio_fallido)
    except RuntimeError:
        pass
    existe = db.profesionales.count_documents({"_id": "prof-003"})
    eventos = db.outbox.count_documents({"entidad_id": "prof-003"})
    print(f"D. Transacción abortada -> profesional: {existe}, eventos: {eventos}")
    assert existe == 0 and eventos == 0

    print("\nOK · MongoDB guarda fichas variables, valida y escribe catálogo + outbox de forma atómica")


if __name__ == "__main__":
    main()
