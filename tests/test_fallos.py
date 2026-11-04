"""Fase 5 · P5: caídas y reinicios. DETIENEN Y LEVANTAN CONTENEDORES.

Solo se ejecutan si se pide explícitamente:

    pytest -v -s tests/test_fallos.py --fallos

Usan `docker compose stop|start|restart <servicio>` desde la raíz del repositorio (variable CONTROL_CMD
para otro mecanismo). Cada prueba deja el sistema como lo encontró (vuelve a levantar lo que detuvo)
aunque falle una aserción. Los IDs (F-xx) corresponden a la matriz de docs/fase5-validacion.md.
"""
import time
import uuid

import pytest

from conftest import controlar, esperar, esperar_salud, estados_cupos, salud

pytestmark = pytest.mark.fallos


@pytest.fixture(autouse=True)
def sistema_sano():
    """Antes y después de cada prueba el sistema debe estar completamente operativo."""
    esperar_salud("ok", timeout=180)
    yield
    esperar_salud("ok", timeout=180)


class Detenido:
    """`with Detenido("typesense"):` detiene el servicio y garantiza que se vuelva a levantar."""

    def __init__(self, servicio: str):
        self.servicio = servicio

    def __enter__(self):
        controlar("stop", self.servicio)
        return self

    def __exit__(self, *_):
        controlar("start", self.servicio)


# ------------------------------------------------------------------ F-01 · P5a reinicio completo
def test_f01_reinicio_conserva_citas_y_descarta_lo_temporal(paciente1, recepcion, nuevo_escenario, record_property):
    esc = nuevo_escenario(cantidad=2)
    cita = paciente1.reservar(esc["cupos"][0]).json()["cita_id"]
    assert recepcion.patch(f"/citas/{cita}/confirmar").status_code == 200
    antes = paciente1.get(f"/citas/{cita}").json()
    assert paciente1.put("/sesiones/filtros", json={"texto": f"antes-del-reinicio-{esc['sufijo']}"}).status_code == 200

    t0 = time.time()
    controlar("restart")  # todos los servicios
    segundos = esperar_salud("ok", timeout=180)
    record_property("f01_recuperacion_s", round(time.time() - t0, 1))
    print(f"\n[F-01] Sistema operativo {time.time() - t0:.1f}s después de reiniciar ({segundos:.1f}s esperando /health)")

    # El JWT sigue siendo válido (mismo JWT_SECRET) y la cita sigue igual: PostgreSQL usa volumen.
    despues = paciente1.get(f"/citas/{cita}").json()
    assert despues["estado"] == "CONFIRMADA" and despues["cupo_id"] == antes["cupo_id"]
    assert [e["tipo"] for e in despues["eventos"]] == [e["tipo"] for e in antes["eventos"]]
    assert estados_cupos(recepcion, esc)[esc["cupos"][0]] == "RESERVADO"
    # El catálogo (MongoDB) y la búsqueda (Typesense) también tienen volumen.
    assert paciente1.get(f"/profesionales/{esc['profesional_id']}").status_code == 200
    assert esperar(lambda: any(x["profesional_id"] == esc["profesional_id"] for x in
                               paciente1.get("/buscar", params={"q": esc["nombre_profesional"]}).json()["resultados"]),
                   timeout=30)[0]
    # Redis no persiste a propósito: los filtros guardados desaparecen.
    assert paciente1.get("/sesiones/filtros").json()["datos"] is None


# ------------------------------------------------------------------ F-02 · P5b búsqueda caída
def test_f02_busqueda_caida_no_impide_reservar_y_se_recupera(admin, anonimo, paciente1, nuevo_escenario,
                                                             record_property):
    esc = nuevo_escenario(cantidad=1)
    pid = esc["profesional_id"]
    assert esperar(lambda: any(x["profesional_id"] == pid for x in
                               anonimo.get("/buscar", params={"q": esc["nombre_profesional"]}).json()["resultados"]))[0]
    nuevo_nombre = f"Dra. Recuperada {esc['sufijo']}"

    with Detenido("typesense"):
        assert esperar(lambda: (salud() or {}).get("estado") == "degradado", timeout=30)[0]
        assert salud()["_status"] == 200  # degradado, no caído: se puede reservar
        r = anonimo.get("/buscar", params={"q": "cardio"})
        assert r.status_code == 503 and r.json()["error"]["codigo"] == "BUSQUEDA_NO_DISPONIBLE"
        # Modo degradado de la web: listado directo desde la fuente oficial.
        r = anonimo.get("/profesionales", params={"q": esc["sufijo"]})
        assert r.status_code == 200 and r.json()["origen"] == "mongodb" and r.json()["total"] == 1
        # La reserva no depende de la búsqueda (RN-02, RN-13).
        assert paciente1.reservar(esc["cupos"][0]).status_code == 201
        # Un cambio en el catálogo se acepta y queda pendiente en el outbox.
        assert admin.patch(f"/profesionales/{pid}", json={"nombre": nuevo_nombre}).status_code == 200
        # Queda pendiente (no se pierde) mientras Typesense no responda.
        time.sleep(3)
        pendientes = admin.get("/sync/estado").json()["pendientes"]
        assert pendientes >= 1
        print(f"\n[F-02] Con Typesense caído: {pendientes} evento(s) pendiente(s) en el outbox")

    t0 = time.time()
    ok, _ = esperar(lambda: any(x["profesional_id"] == pid for x in
                                anonimo.get("/buscar", params={"q": nuevo_nombre}).json().get("resultados", [])),
                    timeout=120, paso=1.0)
    assert ok, "El cambio hecho durante la caída no llegó a la búsqueda"
    record_property("f02_recuperacion_busqueda_s", round(time.time() - t0, 1))
    print(f"[F-02] Cambio pendiente aplicado {time.time() - t0:.1f}s después de levantar Typesense (backoff ≤ 60 s)")
    assert esperar(lambda: admin.get("/sync/estado").json()["pendientes"] == 0, timeout=90)[0]


# ------------------------------------------------------------------ F-03 · P5d estado temporal caído
def test_f03_redis_caido_solo_afecta_lo_temporal(paciente1, nuevo_escenario):
    esc = nuevo_escenario(cantidad=1)
    assert paciente1.put("/sesiones/filtros", json={"texto": "se-perdera"}).status_code == 200

    with Detenido("redis"):
        assert esperar(lambda: (salud() or {}).get("estado") == "degradado", timeout=30)[0]
        r = paciente1.get("/sesiones/filtros")
        assert r.status_code == 503 and r.json()["error"]["codigo"] == "ESTADO_TEMPORAL_NO_DISPONIBLE"
        assert paciente1.reservar(esc["cupos"][0]).status_code == 201
        assert paciente1.get("/buscar", params={"q": "cardio"}).status_code == 200

    assert esperar(lambda: paciente1.get("/sesiones/filtros").status_code == 200, timeout=30)[0]
    assert paciente1.get("/sesiones/filtros").json()["datos"] is None  # efímero por diseño


# ------------------------------------------------------------------ F-04 · P5e agenda caída
def test_f04_postgresql_caido_no_confirma_nada_y_se_recupera(paciente1, anonimo, recepcion, nuevo_escenario,
                                                             record_property):
    esc = nuevo_escenario(cantidad=1)
    clave = str(uuid.uuid4())

    with Detenido("postgres"):
        assert esperar(lambda: (salud() or {}).get("estado") == "no_operativo", timeout=30)[0]
        assert salud()["_status"] == 503
        r = paciente1.reservar(esc["cupos"][0], clave)
        assert r.status_code == 503 and r.json()["error"]["codigo"] == "AGENDA_NO_DISPONIBLE"
        # La búsqueda (proyección) sigue respondiendo: otro dominio, otro gestor.
        assert anonimo.get("/buscar", params={"q": "cardio"}).status_code == 200

    t0 = time.time()
    esperar_salud("ok", timeout=120)
    record_property("f04_recuperacion_agenda_s", round(time.time() - t0, 1))
    # El 503 no dejó nada a medias: el cupo sigue libre y el reintento con la MISMA clave reserva una vez.
    assert estados_cupos(recepcion, esc)[esc["cupos"][0]] == "DISPONIBLE"
    r = paciente1.reservar(esc["cupos"][0], clave)
    assert r.status_code == 201
    assert paciente1.reservar(esc["cupos"][0], clave).status_code == 200


# ------------------------------------------------------------------ F-05 · catálogo caído
def test_f05_mongodb_caido_bloquea_reservas_sin_validar(paciente1, nuevo_escenario):
    esc = nuevo_escenario(cantidad=1)

    with Detenido("mongo"):
        assert esperar(lambda: (salud() or {}).get("estado") == "no_operativo", timeout=40)[0]
        r = paciente1.reservar(esc["cupos"][0])
        assert r.status_code == 503 and r.json()["error"]["codigo"] == "CATALOGO_NO_DISPONIBLE"
        # Las citas existentes se pueden consultar: viven en PostgreSQL (sin nombres del catálogo).
        assert paciente1.get("/citas/mias").status_code == 200

    esperar_salud("ok", timeout=120)
    assert paciente1.reservar(esc["cupos"][0]).status_code == 201


# ------------------------------------------------------------------ F-06 · sincronizador detenido
def test_f06_sincronizador_detenido_acumula_y_no_pierde_eventos(admin, anonimo, nuevo_escenario):
    esc = nuevo_escenario(cantidad=1)
    pid = esc["profesional_id"]
    nombre = f"Dr. Pendiente {esc['sufijo']}"

    with Detenido("sync"):
        assert admin.patch(f"/profesionales/{pid}", json={"nombre": nombre}).status_code == 200
        time.sleep(3)
        estado = admin.get("/sync/estado").json()
        assert estado["pendientes"] >= 1
        assert not any(x["profesional_id"] == pid for x in anonimo.get("/buscar", params={"q": nombre}).json()["resultados"])
        # Sin latido durante más de 15 s, la pantalla técnica marca el worker como detenido.
        assert esperar(lambda: admin.get("/sync/estado").json()["worker"]["activo"] is False, timeout=30, paso=1)[0]

    assert esperar(lambda: any(x["profesional_id"] == pid for x in
                               anonimo.get("/buscar", params={"q": nombre}).json()["resultados"]), timeout=60)[0]
    assert esperar(lambda: admin.get("/sync/estado").json()["worker"]["activo"] is True, timeout=30)[0]


# ------------------------------------------------------------------ F-07 · reconstrucción de la proyección
def test_f07_reindexacion_completa_desde_la_fuente_oficial(admin, anonimo):
    assert esperar(lambda: admin.get("/sync/estado").json()["pendientes"] == 0, timeout=60)[0]
    antes = admin.get("/sync/estado").json()["documentos_en_busqueda"]
    r = admin.post("/sync/reindexar")
    assert r.status_code == 200, r.text
    print(f"\n[F-07] Reindexados {r.json()['reindexados']} profesionales en {r.json()['duracion_ms']} ms")
    assert r.json()["reindexados"] == antes
    assert admin.get("/sync/estado").json()["documentos_en_busqueda"] == antes
    assert anonimo.get("/buscar", params={"q": "Cardiología"}).json()["total"] >= 1
