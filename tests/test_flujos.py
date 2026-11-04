"""Pruebas funcionales e integrales end-to-end contra la API en ejecución.

    docker compose up --build -d
    pip install -r tests/requirements.txt
    pytest -v tests/

Cubren los flujos obligatorios y las pruebas demostrables del enunciado (P1–P4) y reglas RN-01…RN-14.
La prueba P5 (reinicios y caídas) se documenta en tests/README.md porque requiere detener contenedores.
"""
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from conftest import Cliente


def esperar(condicion, timeout=20.0, paso=0.3):
    t0 = time.time()
    while time.time() - t0 < timeout:
        valor = condicion()
        if valor:
            return valor, time.time() - t0
        time.sleep(paso)
    return None, time.time() - t0


def estado_cupo(cliente, escenario, cupo_id):
    r = cliente.get("/cupos", params={"profesional_id": escenario["profesional_id"],
                                      "desde": (escenario["inicio"] - timedelta(hours=1)).isoformat()})
    assert r.status_code == 200
    return next(c["estado"] for c in r.json()["cupos"] if c["cupo_id"] == cupo_id)


# ------------------------------------------------------------------ salud y seguridad
def test_health(anonimo):
    r = anonimo.get("/health")
    assert r.status_code == 200
    cuerpo = r.json()
    assert set(cuerpo["dependencias"]) == {"postgresql", "mongodb", "typesense", "redis"}
    assert "password" not in r.text.lower()


def test_roles_validados_en_api(anonimo, paciente1, escenario):
    assert anonimo.reservar(escenario["cupos"][0]).status_code == 401
    assert paciente1.post("/cupos", json={}).status_code in (403, 422)
    r = paciente1.post("/profesionales", json={"nombre": "Hack", "especialidad_ids": ["x"],
                                               "sedes": [{"sede_id": "x"}], "modalidades": ["PRESENCIAL"]})
    assert r.status_code == 403
    assert r.json()["error"]["codigo"] == "SIN_PERMISO"


def test_validaciones_de_agenda(recepcion, paciente1, escenario):
    # Solape con franjas ya publicadas del mismo profesional → 409 (EXCLUDE USING gist)
    r = recepcion.post("/cupos", json={
        "profesional_id": escenario["profesional_id"], "sede_id": escenario["sede_id"],
        "servicio_id": escenario["servicio_id"], "inicio": escenario["inicio"].isoformat()})
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "CUPO_SOLAPADO"
    # Franja en el pasado → 422
    r = recepcion.post("/cupos", json={
        "profesional_id": escenario["profesional_id"], "sede_id": escenario["sede_id"],
        "servicio_id": escenario["servicio_id"],
        "inicio": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()})
    assert r.status_code == 422
    # Reserva sin Idempotency-Key → 422
    r = paciente1.post("/citas", json={"cupo_id": escenario["cupos"][0]})
    assert r.status_code == 422


# ------------------------------------------------------------------ P1: alta → búsqueda
def test_p1_profesional_registrado_aparece_en_busqueda(anonimo, escenario):
    def aparece():
        r = anonimo.get("/buscar", params={"q": escenario["nombre_profesional"]})
        return r.status_code == 200 and any(x["profesional_id"] == escenario["profesional_id"]
                                            for x in r.json()["resultados"])
    ok, segundos = esperar(aparece)
    assert ok, "El profesional no apareció en la búsqueda"
    print(f"\n[P1] Visible en búsqueda tras {segundos:.2f}s")


# ------------------------------------------------------------------ P3: concurrencia
def test_p3_reservas_simultaneas_solo_una_gana(paciente1, paciente2, recepcion, escenario):
    cupo = escenario["cupos"][0]
    clientes = [paciente1, paciente2] * 5  # 10 intentos simultáneos con claves distintas
    barrera = threading.Barrier(len(clientes))
    resultados = []

    def intentar(c):
        barrera.wait()
        resultados.append(c.reservar(cupo).status_code)

    hilos = [threading.Thread(target=intentar, args=(c,)) for c in clientes]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert resultados.count(201) == 1, resultados
    assert resultados.count(409) == len(clientes) - 1, resultados
    # En la agenda autoritativa hay exactamente una cita activa para el cupo.
    fecha = escenario["inicio"].astimezone(timezone(timedelta(hours=-5))).date().isoformat()
    agenda = recepcion.get("/agenda", params={"fecha": fecha, "profesional_id": escenario["profesional_id"]}).json()
    franja = next(c for c in agenda["cupos"] if c["cupo_id"] == cupo)
    assert franja["estado"] == "RESERVADO" and franja["cita"] is not None


# ------------------------------------------------------------------ idempotencia
def test_reintento_con_misma_clave_no_duplica(paciente1, paciente2, escenario):
    cupo, clave = escenario["cupos"][6], str(uuid.uuid4())
    r1 = paciente1.reservar(cupo, clave)
    r2 = paciente1.reservar(cupo, clave)
    assert r1.status_code == 201 and r2.status_code == 200
    assert r1.json()["cita_id"] == r2.json()["cita_id"]
    # La misma clave usada por otra persona no revela la cita ajena.
    r3 = paciente2.reservar(cupo, clave)
    assert r3.status_code == 409 and r3.json()["error"]["codigo"] == "CLAVE_IDEMPOTENCIA_REUTILIZADA"
    assert "cita_id" not in r3.text
    citas = [c for c in paciente1.get("/citas/mias").json()["citas"] if c["cupo_id"] == cupo]
    assert len(citas) == 1


# ------------------------------------------------------------------ P4: cancelar y volver a reservar
def test_p4_cancelar_libera_y_se_puede_volver_a_reservar(paciente1, paciente2, escenario):
    cupo = escenario["cupos"][1]
    r = paciente1.reservar(cupo)
    assert r.status_code == 201 and r.json()["estado"] == "PENDIENTE"
    cita = r.json()["cita_id"]
    assert estado_cupo(paciente1, escenario, cupo) == "RESERVADO"

    r = paciente1.patch(f"/citas/{cita}/cancelar")
    assert r.status_code == 200 and r.json()["estado"] == "CANCELADA" and r.json()["cupo_liberado"]
    assert estado_cupo(paciente1, escenario, cupo) == "DISPONIBLE"
    assert paciente1.patch(f"/citas/{cita}/cancelar").status_code == 409  # no se cancela dos veces

    r = paciente2.reservar(cupo)
    assert r.status_code == 201
    # El historial de la cita cancelada se conserva (RN-04, RN-06)
    eventos = paciente1.get(f"/citas/{cita}").json()["eventos"]
    assert [e["tipo"] for e in eventos] == ["CREADA", "CANCELADA"]
    assert eventos[1]["estado_anterior"] == "PENDIENTE" and eventos[1]["estado_nuevo"] == "CANCELADA"


# ------------------------------------------------------------------ flujo 4: reprogramación
def test_reprogramacion_atomica(paciente1, paciente2, recepcion, escenario):
    origen, ocupado, destino = escenario["cupos"][2], escenario["cupos"][3], escenario["cupos"][4]
    cita = paciente1.reservar(origen).json()["cita_id"]
    assert paciente2.reservar(ocupado).status_code == 201
    assert recepcion.patch(f"/citas/{cita}/confirmar").status_code == 200

    # A una franja ocupada: 409 y nada cambia
    r = recepcion.patch(f"/citas/{cita}/reprogramar", json={"nuevo_cupo_id": ocupado})
    assert r.status_code == 409
    detalle = paciente1.get(f"/citas/{cita}").json()
    assert detalle["cupo_id"] == origen and detalle["estado"] == "CONFIRMADA"
    assert estado_cupo(recepcion, escenario, origen) == "RESERVADO"
    assert estado_cupo(recepcion, escenario, ocupado) == "RESERVADO"

    # A una franja libre: se mueve atómicamente; recepción conserva el estado
    r = recepcion.patch(f"/citas/{cita}/reprogramar", json={"nuevo_cupo_id": destino})
    assert r.status_code == 200, r.text
    assert r.json()["cupo_id"] == destino and r.json()["estado"] == "CONFIRMADA"
    assert estado_cupo(recepcion, escenario, origen) == "DISPONIBLE"
    assert estado_cupo(recepcion, escenario, destino) == "RESERVADO"
    ev = paciente1.get(f"/citas/{cita}").json()["eventos"][-1]
    assert ev["tipo"] == "REPROGRAMADA" and ev["cupo_anterior_id"] == origen and ev["cupo_nuevo_id"] == destino


def test_confirmar_y_transiciones_invalidas(paciente1, recepcion, escenario):
    cita = paciente1.reservar(escenario["cupos"][7]).json()["cita_id"]
    assert paciente1.patch(f"/citas/{cita}/confirmar").status_code == 403
    assert recepcion.patch(f"/citas/{cita}/confirmar").json()["estado"] == "CONFIRMADA"
    assert recepcion.patch(f"/citas/{cita}/confirmar").status_code == 409
    assert recepcion.patch(f"/citas/{cita}/cancelar").status_code == 200
    r = recepcion.patch(f"/citas/{cita}/confirmar")
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "TRANSICION_INVALIDA"


def test_paciente_solo_ve_sus_citas(paciente1, paciente2, escenario):
    mias = paciente1.get("/citas/mias").json()["citas"]
    assert mias, "paciente1 debería tener citas"
    ajena = mias[0]["cita_id"]
    assert paciente2.get(f"/citas/{ajena}").status_code == 403
    assert paciente2.patch(f"/citas/{ajena}/cancelar").status_code == 403
    assert all(c["cita_id"] != ajena for c in paciente2.get("/citas/mias").json()["citas"])


def test_bloqueo_temporal_hold(paciente1, paciente2, escenario):
    cupo = escenario["cupos"][5]
    r = paciente1.post(f"/cupos/{cupo}/bloquear")
    assert r.status_code == 200 and r.json()["estado"] == "BLOQUEADO" and r.json()["bloqueado_por_mi"]
    r = paciente2.reservar(cupo)
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "CUPO_BLOQUEADO"
    assert paciente1.reservar(cupo).status_code == 201


# ------------------------------------------------------------------ P2 y flujo 5: cambio en la fuente oficial
def test_p2_cambio_de_sede_se_refleja_en_busqueda(admin, anonimo):
    sufijo = uuid.uuid4().hex[:6]
    esp = admin.post("/especialidades", json={"nombre": f"Esp Sync {sufijo}"}).json()["id"]
    sede_a = admin.post("/sedes", json={"nombre": f"Sede A {sufijo}", "direccion_ficticia": "A 1"}).json()["id"]
    sede_b = admin.post("/sedes", json={"nombre": f"Sede B {sufijo}", "direccion_ficticia": "B 2"}).json()["id"]
    prof = admin.post("/profesionales", json={"nombre": f"Dra. Sync {sufijo}", "especialidad_ids": [esp],
                                              "sedes": [{"sede_id": sede_a}], "modalidades": ["PRESENCIAL"]}).json()

    def en_sede(sede):
        def _c():
            r = anonimo.get("/buscar", params={"sede_id": sede})
            return r.status_code == 200 and any(x["profesional_id"] == prof["id"] for x in r.json()["resultados"])
        return _c

    assert esperar(en_sede(sede_a))[0]
    r = admin.patch(f"/profesionales/{prof['id']}", json={"sedes": [{"sede_id": sede_b, "horario_ref": "S"}]})
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["sincronizacion"] == "PENDIENTE"
    ok, segundos = esperar(en_sede(sede_b))
    assert ok, "La búsqueda no reflejó el cambio de sede"
    print(f"\n[P2] Cambio de sede reflejado en la búsqueda tras {segundos:.2f}s")
    assert not en_sede(sede_a)()
    estado = admin.get("/sync/estado").json()
    assert any(e["entidad_id"] == prof["id"] and e["version"] == 2 and e["retraso_ms"] is not None
               for e in estado["ultimos_eventos"])


def test_profesional_desactivado_no_admite_reservas_ni_aparece(admin, recepcion, paciente1, anonimo):
    sufijo = uuid.uuid4().hex[:6]
    esp = admin.post("/especialidades", json={"nombre": f"Esp Baja {sufijo}"}).json()["id"]
    sede = admin.post("/sedes", json={"nombre": f"Sede Baja {sufijo}", "direccion_ficticia": "C 3"}).json()["id"]
    serv = admin.post("/servicios", json={"nombre": f"Serv Baja {sufijo}", "especialidad_id": esp,
                                          "duracion_minutos": 20, "modalidades": ["PRESENCIAL"]}).json()["id"]
    prof = admin.post("/profesionales", json={"nombre": f"Dr. Baja {sufijo}", "especialidad_ids": [esp],
                                              "sedes": [{"sede_id": sede}], "modalidades": ["PRESENCIAL"]}).json()["id"]
    inicio = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=40)
    cupo = recepcion.post("/cupos", json={"profesional_id": prof, "sede_id": sede, "servicio_id": serv,
                                          "inicio": inicio.isoformat()}).json()["cupos"][0]["cupo_id"]
    assert admin.patch(f"/profesionales/{prof}", json={"activo": False}).status_code == 200
    r = paciente1.reservar(cupo)
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "PROFESIONAL_INACTIVO"

    def desaparecio():
        r = anonimo.get("/buscar", params={"q": f"Baja {sufijo}"})
        return r.status_code == 200 and all(x["profesional_id"] != prof for x in r.json()["resultados"])
    assert esperar(desaparecio)[0]
    # La ficha sigue existiendo en la fuente oficial (borrado lógico, RN-11)
    assert anonimo.get(f"/profesionales/{prof}").json()["activo"] is False


# ------------------------------------------------------------------ estado temporal (Redis)
def test_filtros_y_borrador_con_ttl(paciente1, paciente2):
    r = paciente1.put("/sesiones/filtros", json={"texto": "cardio", "especialidad_id": "esp-cardio"})
    assert r.status_code == 200 and r.json()["ttl_segundos"] > 0
    leido = paciente1.get("/sesiones/filtros").json()
    assert leido["datos"]["texto"] == "cardio" and 0 < leido["ttl_restante"] <= r.json()["ttl_segundos"]
    # Cada usuario tiene su propia sesión (la clave sale del token, no de la URL)
    assert paciente2.put("/sesiones/filtros", json={"texto": "pediatria"}).status_code == 200
    assert paciente1.get("/sesiones/filtros").json()["datos"]["texto"] == "cardio"
    assert paciente2.get("/sesiones/filtros").json()["datos"]["texto"] == "pediatria"
