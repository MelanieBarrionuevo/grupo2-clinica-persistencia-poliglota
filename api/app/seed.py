"""Datos semilla ficticios. Idempotente: se puede ejecutar varias veces sin duplicar ni borrar.

    docker compose run --rm seed        (o se ejecuta solo al hacer `docker compose up`)

Crea:
- Usuarios de demo (PostgreSQL): admin, recepcion, paciente1..3 · contraseña: Demo123!
  (prueba1..3 son pacientes reservados para las pruebas automatizadas)
- Catálogo (MongoDB, con eventos de outbox → el sincronizador los indexa en Typesense).
- Franjas de los próximos días hábiles (PostgreSQL) para cada profesional sin agenda futura.
"""
import logging
import time
from datetime import datetime, time as hora, timedelta, timezone

from .config import config
from .db import catalogo, pg
from .modulos import catalogo as cat
from .seguridad import hash_password

log = logging.getLogger("clinica.seed")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

PASSWORD_DEMO = "Demo123!"

USUARIOS = [
    ("usr-admin", "admin", "Ana Administradora", "ADMINISTRADOR"),
    ("usr-recepcion", "recepcion", "Rosa Recepción", "RECEPCIONISTA"),
    ("pac-001", "paciente1", "Pablo Paciente", "PACIENTE"),
    ("pac-002", "paciente2", "Paula Paciente", "PACIENTE"),
    ("pac-003", "paciente3", "Pedro Paciente", "PACIENTE"),
    # Pacientes exclusivos de las pruebas automatizadas: así sus citas no aparecen en las cuentas de la demo.
    ("pac-t01", "prueba1", "Paciente de prueba 1", "PACIENTE"),
    ("pac-t02", "prueba2", "Paciente de prueba 2", "PACIENTE"),
    ("pac-t03", "prueba3", "Paciente de prueba 3", "PACIENTE"),
]

ESPECIALIDADES = [
    ("esp-cardio", "Cardiología", "Corazón y sistema circulatorio"),
    ("esp-pedia", "Pediatría", "Atención de niñas y niños"),
    ("esp-general", "Medicina general", "Consulta de primer nivel"),
    ("esp-derma", "Dermatología", "Piel, cabello y uñas"),
]

SEDES = [
    ("sede-norte", "Sede Norte", "Av. Ficticia N45-12", -0.15, -78.48),
    ("sede-sur", "Sede Sur", "Calle Imaginaria S10-05", -0.28, -78.54),
    ("sede-centro", "Sede Centro", "Pasaje Inventado 123", -0.22, -78.51),
]

SERVICIOS = [
    ("serv-consulta-cardio", "Consulta cardiológica", "esp-cardio", 30, ["PRESENCIAL"]),
    ("serv-control-pedia", "Control de niño sano", "esp-pedia", 30, ["PRESENCIAL", "TELECONSULTA"]),
    ("serv-consulta-general", "Consulta de medicina general", "esp-general", 20, ["PRESENCIAL", "TELECONSULTA"]),
    ("serv-consulta-derma", "Consulta dermatológica", "esp-derma", 30, ["PRESENCIAL"]),
]

PROFESIONALES = [
    dict(id="prof-001", nombre="Dra. Lucía Andrade", especialidad_ids=["esp-cardio"],
         sedes=[{"sede_id": "sede-norte", "horario_ref": "L-V 08:00-13:00"}],
         idiomas=["es", "en"], modalidades=["PRESENCIAL"], atributos={"consultorio": "204"}),
    dict(id="prof-002", nombre="Dr. Martín Cevallos", especialidad_ids=["esp-pedia", "esp-general"],
         sedes=[{"sede_id": "sede-norte", "horario_ref": "L-M 14:00-18:00"},
                {"sede_id": "sede-sur", "horario_ref": "J-V 08:00-12:00"}],
         idiomas=["es"], modalidades=["PRESENCIAL", "TELECONSULTA"], certificaciones=["Neonatología básica"]),
    dict(id="prof-003", nombre="Dra. Sofía Paredes", especialidad_ids=["esp-derma"],
         sedes=[{"sede_id": "sede-centro", "horario_ref": "L-V 09:00-15:00"}],
         idiomas=["es", "fr"], modalidades=["PRESENCIAL"]),
    dict(id="prof-004", nombre="Dr. Andrés Villacís", especialidad_ids=["esp-general"],
         sedes=[{"sede_id": "sede-sur", "horario_ref": "L-V 07:00-12:00"},
                {"sede_id": "sede-centro", "horario_ref": "S 08:00-12:00"}],
         idiomas=["es", "qu"], modalidades=["PRESENCIAL", "TELECONSULTA"], atributos={"atiende_adultos_mayores": "sí"}),
    dict(id="prof-005", nombre="Dra. Elena Moreno", especialidad_ids=["esp-cardio", "esp-general"],
         sedes=[{"sede_id": "sede-centro", "horario_ref": "M-J 14:00-18:00"}],
         idiomas=["es"], modalidades=["PRESENCIAL"]),
]


def esperar_dependencias(intentos: int = 60) -> None:
    for i in range(intentos):
        try:
            with pg() as conn:
                conn.execute("SELECT 1 FROM usuario LIMIT 1")
            catalogo().command("ping")
            return
        except Exception as exc:
            log.info("Esperando PostgreSQL/MongoDB (%s)...", type(exc).__name__)
            time.sleep(2)
    raise SystemExit("Dependencias no disponibles")


def sembrar_usuarios() -> None:
    with pg() as conn, conn.transaction():
        for uid, usuario, nombre, rol in USUARIOS:
            conn.execute(
                "INSERT INTO usuario (id, usuario, nombre, rol, password_hash) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (uid, usuario, nombre, rol, hash_password(PASSWORD_DEMO)),
            )
    log.info("Usuarios de demo listos")


def sembrar_catalogo() -> None:
    db = catalogo()
    for eid, nombre, desc in ESPECIALIDADES:
        if not db.especialidades.find_one({"_id": eid}):
            cat.crear_especialidad(cat.EspecialidadIn(nombre=nombre, descripcion=desc), id_fijo=eid)
    for sid, nombre, direccion, lat, lon in SEDES:
        if not db.sedes.find_one({"_id": sid}):
            cat.crear_sede(cat.SedeIn(nombre=nombre, direccion_ficticia=direccion, latitud=lat, longitud=lon), id_fijo=sid)
    for sid, nombre, esp, dur, mods in SERVICIOS:
        if not db.servicios.find_one({"_id": sid}):
            cat.crear_servicio(cat.ServicioIn(nombre=nombre, especialidad_id=esp, duracion_minutos=dur, modalidades=mods),
                               id_fijo=sid)
    for p in PROFESIONALES:
        if not db.profesionales.find_one({"_id": p["id"]}):
            datos = {k: v for k, v in p.items() if k != "id"}
            cat.crear_profesional(cat.ProfesionalIn(**datos), id_fijo=p["id"])
    log.info("Catálogo listo (los eventos de outbox quedan para el sincronizador)")


def _dias_habiles(n: int):
    hoy = datetime.now(config.zona_horaria).date()
    d = hoy
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
            yield d


def sembrar_cupos(dias: int = 5) -> None:
    db = catalogo()
    servicios = {s["_id"]: s for s in db.servicios.find({"activo": True})}
    with pg() as conn, conn.transaction():
        for p in db.profesionales.find({"activo": True}):
            futuro = conn.execute(
                "SELECT 1 FROM cupo WHERE profesional_id=%s AND inicio > now() LIMIT 1", (p["_id"],)
            ).fetchone()
            if futuro:
                continue
            serv = next((s for s in servicios.values() if s["especialidad_id"] == p["especialidad_ids"][0]), None)
            if serv is None:
                continue
            duracion = timedelta(minutes=serv["duracion_minutos"])
            for i, dia in enumerate(_dias_habiles(dias)):
                sede = p["sedes"][i % len(p["sedes"])]["sede_id"]
                inicio = datetime.combine(dia, hora(9, 0), tzinfo=config.zona_horaria)
                for k in range(6):
                    ini = inicio + k * duracion
                    conn.execute(
                        "INSERT INTO cupo (profesional_id, sede_id, servicio_id, inicio, fin) VALUES (%s,%s,%s,%s,%s)",
                        (p["_id"], sede, serv["_id"], ini.astimezone(timezone.utc), (ini + duracion).astimezone(timezone.utc)),
                    )
    log.info("Franjas de demostración listas")


def main() -> None:
    esperar_dependencias()
    sembrar_usuarios()
    sembrar_catalogo()
    sembrar_cupos()
    log.info("Semilla completada. Contraseña de todos los usuarios demo: %s", PASSWORD_DEMO)


if __name__ == "__main__":
    main()
