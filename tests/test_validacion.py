"""Fase 5 · Validación: pruebas funcionales, integrales y de consistencia que amplían test_flujos.py.

    pytest -v -s tests/                    # funcionales + consistencia (no detiene contenedores)
    pytest -v -s tests/ --fallos           # además P5: caídas y reinicios (ver test_fallos.py)

Cada prueba crea su propio catálogo y sus franjas, así que se pueden repetir sin limpiar datos.
Los IDs (V-xx) corresponden a la matriz de docs/fase5-validacion.md.
"""
import statistics
import threading
import uuid
from datetime import datetime, timedelta

import pytest
import requests

from conftest import API, esperar, estados_cupos


def en_paralelo(funciones):
    """Lanza todas las funciones a la vez (barrera) y devuelve sus resultados en orden."""
    barrera = threading.Barrier(len(funciones))
    resultados = [None] * len(funciones)

    def correr(i, f):
        barrera.wait()
        resultados[i] = f()

    hilos = [threading.Thread(target=correr, args=(i, f)) for i, f in enumerate(funciones)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    return resultados


def citas_activas_por_cupo(recepcion, esc) -> dict[int, int]:
    r = recepcion.get("/citas", params={"profesional_id": esc["profesional_id"], "limite": 500})
    assert r.status_code == 200, r.text
    activas: dict[int, int] = {}
    for c in r.json()["citas"]:
        if c["estado"] in ("PENDIENTE", "CONFIRMADA"):
            activas[c["cupo_id"]] = activas.get(c["cupo_id"], 0) + 1
    return activas


def verificar_invariante_agenda(recepcion, esc) -> None:
    """Consistencia cupo ↔ cita en PostgreSQL: RESERVADO ⇔ exactamente una cita activa (RN-01)."""
    estados = estados_cupos(recepcion, esc)
    activas = citas_activas_por_cupo(recepcion, esc)
    for cupo_id, estado in estados.items():
        n = activas.get(cupo_id, 0)
        assert n <= 1, f"Cupo {cupo_id} con {n} citas activas"
        assert (estado == "RESERVADO") == (n == 1), f"Cupo {cupo_id} en {estado} con {n} citas activas"


# ================================================================== P1 · retraso de la proyección
def test_v01_p1_retraso_de_proyeccion_medido(admin, anonimo, record_property):
    """P1 repetido 5 veces: profesional nuevo → visible en /buscar. Se registra el retraso."""
    retrasos = []
    for _ in range(5):
        sufijo = uuid.uuid4().hex[:6]
        esp = admin.post("/especialidades", json={"nombre": f"Esp Retraso {sufijo}"}).json()["id"]
        sede = admin.post("/sedes", json={"nombre": f"Sede Retraso {sufijo}", "direccion_ficticia": "R 1"}).json()["id"]
        r = admin.post("/profesionales", json={"nombre": f"Dr. Retraso {sufijo}", "especialidad_ids": [esp],
                                               "sedes": [{"sede_id": sede}], "modalidades": ["PRESENCIAL"]})
        assert r.status_code == 201 and r.json()["sincronizacion"] == "PENDIENTE"
        pid = r.json()["id"]

        def visible():
            b = anonimo.get("/buscar", params={"q": f"Retraso {sufijo}"})
            return b.status_code == 200 and any(x["profesional_id"] == pid for x in b.json()["resultados"])
        ok, segundos = esperar(visible, timeout=30, paso=0.1)
        assert ok, f"{pid} no apareció en la búsqueda en 30 s"
        retrasos.append(segundos)
    record_property("p1_retraso_promedio_s", round(statistics.mean(retrasos), 2))
    record_property("p1_retraso_maximo_s", round(max(retrasos), 2))
    print(f"\n[V-01] Retraso alta → búsqueda: promedio {statistics.mean(retrasos):.2f}s, máx {max(retrasos):.2f}s")
    # Con SYNC_INTERVALO_MS=1000 el retraso esperado es de pocos segundos.
    assert max(retrasos) < 15


def test_v02_retraso_registrado_en_outbox(admin):
    """El sincronizador deja creado_en, procesado_en y retraso_ms por evento (pantalla técnica)."""
    def hay_procesados():
        e = admin.get("/sync/estado").json()
        return e if any(x["retraso_ms"] is not None for x in e["ultimos_eventos"]) else None
    estado, _ = esperar(hay_procesados, timeout=20)
    assert estado, "No hay eventos procesados con retraso medido"
    assert estado["worker"]["activo"] is True
    assert estado["pendientes_con_error"] == 0
    assert "password" not in str(estado).lower() and "mongodb://" not in str(estado)


def test_v03_catalogo_y_busqueda_coinciden_tras_sincronizar(admin, anonimo):
    """Consistencia eventual: con el outbox vacío, la proyección refleja exactamente la fuente oficial."""
    assert esperar(lambda: admin.get("/sync/estado").json()["pendientes"] == 0, timeout=30)[0]
    indexados: set[str] = set()
    for pagina in range(1, 101):
        r = anonimo.get("/buscar", params={"ordenar": "nombre", "pagina": pagina, "por_pagina": 50}).json()
        indexados |= {x["profesional_id"] for x in r["resultados"]}
        if len(r["resultados"]) < 50:
            break
    todos = admin.get("/profesionales", params={"incluir_inactivos": True}).json()["profesionales"]
    activos = {p["id"] for p in todos if p["activo"]}
    inactivos = {p["id"] for p in todos if not p["activo"]}
    assert activos <= indexados, f"Activos sin proyectar: {sorted(activos - indexados)[:5]}"
    assert not (inactivos & indexados), f"Inactivos aún en la búsqueda: {sorted(inactivos & indexados)[:5]}"
    if len(todos) < 200:  # el listado oficial devuelve hasta 200: solo entonces se compara el total exacto
        assert indexados == activos


# ================================================================== concurrencia e idempotencia
def test_v04_misma_clave_en_paralelo_crea_una_sola_cita(paciente1, recepcion, nuevo_escenario):
    """Ocho reintentos simultáneos con la MISMA Idempotency-Key → una sola cita (RN-03)."""
    esc = nuevo_escenario(cantidad=1)
    clave = str(uuid.uuid4())
    respuestas = en_paralelo([lambda: paciente1.reservar(esc["cupos"][0], clave) for _ in range(8)])
    codigos = sorted(r.status_code for r in respuestas)
    assert codigos.count(201) == 1 and codigos.count(200) == 7, codigos
    assert len({r.json()["cita_id"] for r in respuestas}) == 1
    verificar_invariante_agenda(recepcion, esc)


def test_v05_reprogramar_y_reservar_compiten_por_el_mismo_destino(paciente1, paciente2, recepcion, nuevo_escenario):
    """Recepción reprograma hacia un cupo que otro paciente reserva al mismo tiempo: gana uno solo."""
    esc = nuevo_escenario(cantidad=2)
    origen, destino = esc["cupos"]
    cita = paciente1.reservar(origen).json()["cita_id"]
    repro, reserva = en_paralelo([
        lambda: recepcion.patch(f"/citas/{cita}/reprogramar", json={"nuevo_cupo_id": destino}),
        lambda: paciente2.reservar(destino),
    ])
    assert sorted([repro.status_code, reserva.status_code]) in ([200, 409], [201, 409]), (repro.text, reserva.text)
    detalle = paciente1.get(f"/citas/{cita}").json()
    if repro.status_code == 200:
        assert detalle["cupo_id"] == destino
    else:  # la reprogramación perdió: la cita sigue intacta en su cupo original
        assert detalle["cupo_id"] == origen
    verificar_invariante_agenda(recepcion, esc)


def test_v06_cancelaciones_simultaneas_un_solo_evento(paciente1, recepcion, nuevo_escenario):
    """Paciente y recepción cancelan a la vez: una gana, la otra 409, y hay UN evento CANCELADA."""
    esc = nuevo_escenario(cantidad=1)
    cita = paciente1.reservar(esc["cupos"][0]).json()["cita_id"]
    respuestas = en_paralelo([lambda: paciente1.patch(f"/citas/{cita}/cancelar"),
                              lambda: recepcion.patch(f"/citas/{cita}/cancelar")])
    assert sorted(r.status_code for r in respuestas) == [200, 409]
    tipos = [e["tipo"] for e in paciente1.get(f"/citas/{cita}").json()["eventos"]]
    assert tipos == ["CREADA", "CANCELADA"]
    verificar_invariante_agenda(recepcion, esc)


def test_v07_carga_concurrente_mantiene_invariante(paciente1, paciente2, recepcion, nuevo_escenario):
    """20 reservas simultáneas repartidas en 4 franjas: exactamente 4 éxitos y agenda consistente."""
    esc = nuevo_escenario(cantidad=4)
    pacientes = [paciente1, paciente2]
    tareas = [(lambda c=c, p=pacientes[i % 2]: p.reservar(c)) for i, c in enumerate(esc["cupos"] * 5)]
    codigos = [r.status_code for r in en_paralelo(tareas)]
    assert codigos.count(201) == 4 and codigos.count(409) == 16, codigos
    assert set(estados_cupos(recepcion, esc).values()) == {"RESERVADO"}
    verificar_invariante_agenda(recepcion, esc)


# ================================================================== la API no confía en el navegador (RS-04)
def test_v08_api_ignora_estado_y_horario_enviados_por_el_navegador(paciente1, paciente2, recepcion, nuevo_escenario):
    esc = nuevo_escenario(cantidad=2)
    # Un estado enviado por el navegador se ignora: la cita del paciente nace PENDIENTE.
    r = paciente1.post("/citas", json={"cupo_id": esc["cupos"][0], "estado": "CONFIRMADA"},
                       headers={"Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 201 and r.json()["estado"] == "PENDIENTE"
    # Un paciente no puede reservar a nombre de otro.
    r = paciente1.reservar(esc["cupos"][1], paciente_id=paciente2.usuario["id"])
    assert r.status_code == 403
    # El servicio debe coincidir con el de la franja.
    r = paciente1.reservar(esc["cupos"][1], servicio_id="serv-que-no-corresponde")
    assert r.status_code == 422 and r.json()["error"]["codigo"] == "SERVICIO_NO_COINCIDE"
    # Al publicar, el fin lo calcula la API con la duración del catálogo, aunque el navegador envíe otro.
    inicio = esc["inicio"] + timedelta(days=1)
    r = recepcion.post("/cupos", json={"profesional_id": esc["profesional_id"], "sede_id": esc["sede_id"],
                                       "servicio_id": esc["servicio_id"], "inicio": inicio.isoformat(),
                                       "fin": (inicio + timedelta(hours=5)).isoformat()})
    assert r.status_code == 201
    franja = r.json()["cupos"][0]
    duracion = datetime.fromisoformat(franja["fin"]) - datetime.fromisoformat(franja["inicio"])
    assert duracion == timedelta(minutes=esc["duracion"])


def test_v09_validaciones_de_entrada(paciente1, recepcion, nuevo_escenario):
    esc = nuevo_escenario(cantidad=1)
    casos = [
        (paciente1.post("/citas", json={"cupo_id": esc["cupos"][0]}, headers={"Idempotency-Key": "no-es-uuid"}), 422),
        (paciente1.reservar(99_999_999), 404),
        (paciente1.post("/citas", json={"cupo_id": "abc"}, headers={"Idempotency-Key": str(uuid.uuid4())}), 422),
        (recepcion.post("/cupos", json={"profesional_id": esc["profesional_id"], "sede_id": esc["sede_id"],
                                        "servicio_id": esc["servicio_id"], "inicio": "2030-01-10T09:00:00"}), 422),
        (paciente1.get("/buscar", params={"sede_id": "a`b"}), 422),
        (paciente1.get("/buscar", params={"q": "x" * 81}), 422),
    ]
    for respuesta, esperado in casos:
        assert respuesta.status_code == esperado, (respuesta.request.url, respuesta.status_code, respuesta.text)
        assert set(respuesta.json()) == {"error"} and {"codigo", "mensaje"} <= set(respuesta.json()["error"])


def test_v10_matriz_de_permisos(anonimo, paciente1, recepcion, admin, nuevo_escenario):
    """Autorización en la API según la matriz de la Fase 1 (no solo ocultando botones en la web)."""
    esc = nuevo_escenario(cantidad=1)
    cita = paciente1.reservar(esc["cupos"][0]).json()["cita_id"]
    casos = [
        (anonimo.get("/citas/mias"), 401),
        (paciente1.get("/agenda", params={"fecha": "2030-01-10"}), 403),
        (paciente1.get("/citas"), 403),
        (paciente1.get("/sync/estado"), 403),
        (paciente1.post("/sync/reindexar"), 403),
        (paciente1.patch(f"/citas/{cita}/confirmar"), 403),
        (recepcion.post("/especialidades", json={"nombre": "No permitido"}), 403),
        (recepcion.post("/sync/reindexar"), 403),
        (admin.patch(f"/citas/{cita}/cancelar"), 403),
        (admin.post("/citas", json={"cupo_id": esc["cupos"][0]}, headers={"Idempotency-Key": str(uuid.uuid4())}), 403),
    ]
    for respuesta, esperado in casos:
        assert respuesta.status_code == esperado, (respuesta.request.method, respuesta.request.url, respuesta.status_code)


def test_v11_token_manipulado_es_rechazado(paciente1):
    token = paciente1.s.headers["Authorization"].split()[1]
    cabecera, cuerpo, firma = token.split(".")
    falso = f"{cabecera}.{cuerpo}.{firma[::-1]}"
    r = requests.get(f"{API}/citas/mias", headers={"Authorization": f"Bearer {falso}"}, timeout=10)
    assert r.status_code == 401 and r.json()["error"]["codigo"] == "TOKEN_INVALIDO"


# ================================================================== reglas de estado
def test_v12_paciente_reprograma_vuelve_a_pendiente(paciente3, recepcion, nuevo_escenario):
    """Decisión de Fase 4: recepción crea CONFIRMADA; si el paciente reprograma, vuelve a PENDIENTE."""
    esc = nuevo_escenario(cantidad=2)
    r = recepcion.reservar(esc["cupos"][0], paciente_id=paciente3.usuario["id"])
    assert r.status_code == 201 and r.json()["estado"] == "CONFIRMADA"
    cita = r.json()["cita_id"]
    r = paciente3.patch(f"/citas/{cita}/reprogramar", json={"nuevo_cupo_id": esc["cupos"][1]})
    assert r.status_code == 200 and r.json()["estado"] == "PENDIENTE"
    ev = paciente3.get(f"/citas/{cita}").json()["eventos"]
    assert [e["tipo"] for e in ev] == ["CREADA", "REPROGRAMADA"]
    assert ev[1]["estado_anterior"] == "CONFIRMADA" and ev[1]["estado_nuevo"] == "PENDIENTE"
    verificar_invariante_agenda(recepcion, esc)


def test_v13_cita_cancelada_no_se_reactiva(paciente1, recepcion, nuevo_escenario):
    esc = nuevo_escenario(cantidad=2)
    cita = paciente1.reservar(esc["cupos"][0]).json()["cita_id"]
    assert paciente1.patch(f"/citas/{cita}/cancelar").status_code == 200
    for r in (recepcion.patch(f"/citas/{cita}/confirmar"),
              paciente1.patch(f"/citas/{cita}/reprogramar", json={"nuevo_cupo_id": esc["cupos"][1]})):
        assert r.status_code == 409 and r.json()["error"]["codigo"] == "TRANSICION_INVALIDA"
    assert estados_cupos(recepcion, esc)[esc["cupos"][1]] == "DISPONIBLE"


def test_v14_hold_ajeno_no_se_libera_y_expira_en_el_ttl(paciente1, paciente2, nuevo_escenario):
    esc = nuevo_escenario(cantidad=1)
    cupo = esc["cupos"][0]
    r = paciente1.post(f"/cupos/{cupo}/bloquear")
    assert r.status_code == 200
    ttl = r.json()["ttl_segundos"]
    restante = (datetime.fromisoformat(r.json()["bloqueado_hasta"]) - datetime.now().astimezone()).total_seconds()
    assert 0 < restante <= ttl + 5, "El vencimiento del hold no corresponde a TTL_BLOQUEO_CUPO_SEGUNDOS"
    assert paciente2.post(f"/cupos/{cupo}/liberar").status_code == 403
    assert paciente1.post(f"/cupos/{cupo}/liberar").status_code == 200
    assert paciente2.reservar(cupo).status_code == 201


def test_v15_reintento_tras_cancelar_no_recrea_la_cita(paciente1, recepcion, nuevo_escenario):
    """Reenviar la petición original (misma clave) después de cancelar devuelve la cita cancelada, no una nueva."""
    esc = nuevo_escenario(cantidad=1)
    clave = str(uuid.uuid4())
    cita = paciente1.reservar(esc["cupos"][0], clave).json()["cita_id"]
    paciente1.patch(f"/citas/{cita}/cancelar")
    r = paciente1.reservar(esc["cupos"][0], clave)
    assert r.status_code == 200 and r.json()["cita_id"] == cita and r.json()["estado"] == "CANCELADA"
    assert estados_cupos(recepcion, esc)[esc["cupos"][0]] == "DISPONIBLE"


# ================================================================== errores y salud (RS-06)
def test_v16_errores_uniformes_y_sin_secretos(anonimo, paciente1):
    respuestas = [anonimo.get("/ruta-que-no-existe"), paciente1.reservar(99_999_998), anonimo.get("/health")]
    for r in respuestas[:2]:
        assert set(r.json()) == {"error"}
    texto = " ".join(r.text.lower() for r in respuestas)
    for secreto in ("password", "jwt_secret", "api_key", "requirepass", "mongodb://", "postgresql://"):
        assert secreto not in texto


def test_v18_busqueda_tolera_errores_y_filtra(anonimo):
    """Lo que justificó elegir Typesense en la Fase 2: texto con error de tipeo + filtros + facetas."""
    r = anonimo.get("/buscar", params={"q": "cardiolgia"}).json()  # falta una "o"
    assert r["origen"] == "typesense" and r["total"] >= 1
    assert all("Cardiología" in x["especialidades"] for x in r["resultados"])
    r = anonimo.get("/buscar", params={"especialidad_id": "esp-general", "sede_id": "sede-sur"}).json()
    assert r["total"] >= 1
    assert all("Medicina general" in x["especialidades"] and "Sede Sur" in x["sedes"] for x in r["resultados"])
    assert "especialidades" in r["facetas"] and "sedes" in r["facetas"]


def test_v19_busqueda_ordena_por_cercania(anonimo):
    """H-01: orden por cercanía con las coordenadas ficticias de las sedes (geopoint en Typesense)."""
    norte = (-0.15, -78.48)  # coordenadas de la Sede Norte en la semilla
    r = anonimo.get("/buscar", params={"cerca": f"{norte[0]},{norte[1]}", "por_pagina": 50})
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["ordenado_por"] == "cercania"
    con_distancia = [x for x in cuerpo["resultados"] if x["distancia_km"] is not None]
    assert con_distancia, "Ningún resultado trae distancia: ¿la proyección tiene el campo ubicaciones?"
    distancias = [x["distancia_km"] for x in con_distancia]
    assert distancias == sorted(distancias), distancias
    assert "Sede Norte" in con_distancia[0]["sedes"] and con_distancia[0]["distancia_km"] < 1
    # Los que no tienen sede con coordenadas van al final, nunca antes de uno con distancia.
    vistos_sin = False
    for x in cuerpo["resultados"]:
        vistos_sin = vistos_sin or x["distancia_km"] is None
        assert not (vistos_sin and x["distancia_km"] is not None)
    for invalido in ("999,1", "abc", "-0.15"):
        assert anonimo.get("/buscar", params={"cerca": invalido}).status_code == 422


@pytest.mark.parametrize("ruta", ["/especialidades", "/sedes", "/servicios", "/profesionales", "/buscar", "/cupos"])
def test_v17_lecturas_publicas_responden(anonimo, ruta):
    r = anonimo.get(ruta)
    assert r.status_code == 200, r.text
