import os
import shlex
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

API = os.getenv("API_URL", "http://localhost:8000/api/v1")
PASSWORD = os.getenv("DEMO_PASSWORD", "Demo123!")
RAIZ = Path(__file__).resolve().parents[1]
# Comando para detener/levantar servicios en las pruebas de fallos (P5).
# Por defecto Docker Compose desde la raíz del repositorio. {accion}: stop|start|restart; {servicio}: nombre en Compose
# (vacío = todos). Si el comando devuelve el código 3, la prueba se omite (servicio no controlable en ese entorno).
CONTROL_CMD = os.getenv("CONTROL_CMD", "docker compose {accion} {servicio}")


# ------------------------------------------------------------------ opciones de pytest
def pytest_addoption(parser):
    parser.addoption("--fallos", action="store_true", default=False,
                     help="Ejecuta también las pruebas de fallos (P5): detienen y levantan contenedores")


def pytest_configure(config):
    config.addinivalue_line("markers", "fallos: detiene y levanta servicios (P5); requiere --fallos o FALLOS=1")


def pytest_collection_modifyitems(config, items):
    # Las pruebas de fallos van al final: detienen y reinician servicios.
    items.sort(key=lambda item: "fallos" in item.keywords)
    if config.getoption("--fallos") or os.getenv("FALLOS") == "1":
        return
    omitir = pytest.mark.skip(reason="Prueba de fallos: ejecutar con --fallos (detiene contenedores)")
    for item in items:
        if "fallos" in item.keywords:
            item.add_marker(omitir)


# ------------------------------------------------------------------ utilidades compartidas
def esperar(condicion, timeout=20.0, paso=0.3):
    """Repite `condicion()` hasta que sea verdadera. Devuelve (valor, segundos)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            valor = condicion()
        except requests.RequestException:
            valor = None
        if valor:
            return valor, time.time() - t0
        time.sleep(paso)
    return None, time.time() - t0


def controlar(accion: str, servicio: str = "") -> None:
    """Detiene/levanta/reinicia un servicio con CONTROL_CMD (por defecto `docker compose`)."""
    cmd = CONTROL_CMD.format(accion=accion, servicio=servicio).strip()
    r = subprocess.run(shlex.split(cmd), cwd=RAIZ, capture_output=True, text=True, timeout=180)
    if r.returncode == 3:
        pytest.skip(f"'{accion} {servicio or 'todo'}' no es controlable en este entorno")
    assert r.returncode == 0, f"Falló `{cmd}`: {r.stderr[-500:]}"


def salud() -> dict | None:
    try:
        r = requests.get(f"{API}/health", timeout=15)
        return r.json() | {"_status": r.status_code}
    except (requests.RequestException, ValueError):
        return None


def esperar_salud(estado: str = "ok", timeout: float = 120.0) -> float:
    """Espera a que /health informe `estado`. Devuelve los segundos que tardó."""
    ok, segundos = esperar(lambda: (salud() or {}).get("estado") == estado, timeout=timeout, paso=1.0)
    assert ok, f"/health no llegó a '{estado}' en {timeout:.0f}s: {salud()}"
    return segundos


# Lo que las pruebas crean se registra aquí y se limpia al terminar la sesión (fixture `limpieza_final`).
CREADOS: list[tuple[str, str]] = []
CITAS: list[str] = []
RUTAS_CATALOGO = ("/especialidades", "/sedes", "/servicios", "/profesionales")


class Cliente:
    def __init__(self, usuario: str | None = None):
        self.s = requests.Session()
        if usuario:
            r = self.s.post(f"{API}/auth/login", json={"usuario": usuario, "password": PASSWORD}, timeout=10)
            assert r.status_code == 200, r.text
            self.usuario = r.json()["usuario"]
            self.s.headers["Authorization"] = f"Bearer {r.json()['token']}"

    def get(self, ruta, **kw):
        return self.s.get(f"{API}{ruta}", timeout=15, **kw)

    def post(self, ruta, **kw):
        r = self.s.post(f"{API}{ruta}", timeout=15, **kw)
        if ruta in RUTAS_CATALOGO and r.status_code == 201:
            CREADOS.append((ruta, r.json()["id"]))
        elif ruta == "/citas" and r.status_code in (200, 201):
            CITAS.append(r.json()["cita_id"])
        return r

    def patch(self, ruta, **kw):
        return self.s.patch(f"{API}{ruta}", timeout=15, **kw)

    def put(self, ruta, **kw):
        return self.s.put(f"{API}{ruta}", timeout=15, **kw)

    def delete(self, ruta, **kw):
        return self.s.delete(f"{API}{ruta}", timeout=15, **kw)

    def reservar(self, cupo_id: int, clave: str | None = None, **extra):
        return self.post("/citas", json={"cupo_id": cupo_id, **extra},
                         headers={"Idempotency-Key": clave or str(uuid.uuid4())})


@pytest.fixture(scope="session")
def admin():
    return Cliente("admin")


@pytest.fixture(scope="session", autouse=True)
def limpieza_final():
    """Al terminar, deja la demo como estaba: nada de lo creado por las pruebas queda visible.

    - Las citas de prueba que sigan activas se cancelan (no aparecen en «Citas pendientes» de Recepción).
    - Lo creado en el catálogo se desactiva (borrado lógico): sale de la búsqueda y de Administración.
    No se borra nada: el historial se conserva, como exige el diseño (RN-04, RN-11).
    """
    yield
    if not CITAS and not CREADOS:
        return
    try:
        recepcion, admin = Cliente("recepcion"), Cliente("admin")
    except (AssertionError, requests.RequestException):
        return  # API caída al final: no se puede limpiar, pero tampoco se rompe la sesión
    for cita in set(CITAS):
        try:
            recepcion.patch(f"/citas/{cita}/cancelar")
        except requests.RequestException:
            pass
    orden = {"/profesionales": 0, "/servicios": 1, "/especialidades": 2, "/sedes": 3}
    for ruta, ident in sorted(set(CREADOS), key=lambda x: orden[x[0]]):
        try:
            admin.patch(f"{ruta}/{ident}", json={"activo": False})
        except requests.RequestException:
            pass


@pytest.fixture(scope="session")
def recepcion():
    return Cliente("recepcion")


# Las pruebas usan pacientes propios (prueba1..3, creados por la semilla) para no llenar de citas de prueba
# las cuentas paciente1..3 que se muestran en la defensa. PACIENTES_PRUEBA permite cambiarlos.
PACIENTES = os.getenv("PACIENTES_PRUEBA", "prueba1,prueba2,prueba3").split(",")


@pytest.fixture(scope="session")
def paciente1():
    return Cliente(PACIENTES[0])


@pytest.fixture(scope="session")
def paciente2():
    return Cliente(PACIENTES[1])


@pytest.fixture(scope="session")
def paciente3():
    return Cliente(PACIENTES[2])


@pytest.fixture(scope="session")
def anonimo():
    return Cliente()


@pytest.fixture(scope="session")
def escenario(admin, recepcion):
    """Crea un catálogo propio de la prueba (nombres únicos) y publica franjas futuras."""
    sufijo = uuid.uuid4().hex[:6]
    esp = admin.post("/especialidades", json={"nombre": f"Prueba Esp {sufijo}"})
    assert esp.status_code == 201, esp.text
    sede = admin.post("/sedes", json={"nombre": f"Sede Prueba {sufijo}", "direccion_ficticia": "Calle Test 1"})
    assert sede.status_code == 201, sede.text
    serv = admin.post("/servicios", json={"nombre": f"Servicio prueba {sufijo}", "especialidad_id": esp.json()["id"],
                                          "duracion_minutos": 30, "modalidades": ["PRESENCIAL"]})
    assert serv.status_code == 201, serv.text
    prof = admin.post("/profesionales", json={
        "nombre": f"Dr. Prueba {sufijo}", "especialidad_ids": [esp.json()["id"]],
        "sedes": [{"sede_id": sede.json()["id"], "horario_ref": "L-V"}],
        "idiomas": ["es"], "modalidades": ["PRESENCIAL"], "atributos": {"consultorio": "T-1"},
    })
    assert prof.status_code == 201, prof.text
    # Franjas a partir de una fecha lejana y única para no chocar con la semilla.
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(days=30)
    cupos = recepcion.post("/cupos", json={
        "profesional_id": prof.json()["id"], "sede_id": sede.json()["id"], "servicio_id": serv.json()["id"],
        "inicio": base.isoformat(), "cantidad": 8,
    })
    assert cupos.status_code == 201, cupos.text
    return {
        "sufijo": sufijo, "especialidad_id": esp.json()["id"], "sede_id": sede.json()["id"],
        "servicio_id": serv.json()["id"], "profesional_id": prof.json()["id"],
        "nombre_profesional": prof.json()["nombre"], "inicio": base,
        "cupos": [c["cupo_id"] for c in cupos.json()["cupos"]],
    }


def crear_escenario(admin, recepcion, cantidad: int = 6, dias: int = 30, duracion: int = 30) -> dict:
    """Catálogo propio (nombres únicos) + `cantidad` franjas consecutivas dentro de `dias` días."""
    sufijo = uuid.uuid4().hex[:6]
    esp = admin.post("/especialidades", json={"nombre": f"Esp F5 {sufijo}"})
    assert esp.status_code == 201, esp.text
    sede = admin.post("/sedes", json={"nombre": f"Sede F5 {sufijo}", "direccion_ficticia": "Calle Validación 5"})
    assert sede.status_code == 201, sede.text
    serv = admin.post("/servicios", json={"nombre": f"Servicio F5 {sufijo}", "especialidad_id": esp.json()["id"],
                                          "duracion_minutos": duracion, "modalidades": ["PRESENCIAL"]})
    assert serv.status_code == 201, serv.text
    prof = admin.post("/profesionales", json={
        "nombre": f"Dra. Validación {sufijo}", "especialidad_ids": [esp.json()["id"]],
        "sedes": [{"sede_id": sede.json()["id"], "horario_ref": "L-V"}], "modalidades": ["PRESENCIAL"],
    })
    assert prof.status_code == 201, prof.text
    # Minuto aleatorio dentro de un día lejano: dos ejecuciones no comparten franjas.
    base = (datetime.now(timezone.utc).replace(second=0, microsecond=0)
            + timedelta(days=dias, minutes=int(sufijo, 16) % 600))
    cupos = recepcion.post("/cupos", json={
        "profesional_id": prof.json()["id"], "sede_id": sede.json()["id"], "servicio_id": serv.json()["id"],
        "inicio": base.isoformat(), "cantidad": cantidad,
    })
    assert cupos.status_code == 201, cupos.text
    return {
        "sufijo": sufijo, "especialidad_id": esp.json()["id"], "sede_id": sede.json()["id"],
        "servicio_id": serv.json()["id"], "profesional_id": prof.json()["id"],
        "nombre_profesional": prof.json()["nombre"], "inicio": base, "duracion": duracion,
        "cupos": [c["cupo_id"] for c in cupos.json()["cupos"]],
    }


@pytest.fixture
def nuevo_escenario(admin, recepcion):
    """Fábrica: cada prueba que lo pida obtiene su propio profesional y sus propias franjas."""
    return lambda **kw: crear_escenario(admin, recepcion, **kw)


def estados_cupos(cliente, esc) -> dict[int, str]:
    """Estado de cada franja del escenario leído de la agenda autoritativa (PostgreSQL)."""
    r = cliente.get("/cupos", params={"profesional_id": esc["profesional_id"],
                                      "desde": (esc["inicio"] - timedelta(hours=1)).isoformat()})
    assert r.status_code == 200, r.text
    return {c["cupo_id"]: c["estado"] for c in r.json()["cupos"] if c["cupo_id"] in esc["cupos"]}
