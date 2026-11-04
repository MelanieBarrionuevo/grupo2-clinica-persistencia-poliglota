import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests

API = os.getenv("API_URL", "http://localhost:8000/api/v1")
PASSWORD = os.getenv("DEMO_PASSWORD", "Demo123!")


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
        return self.s.post(f"{API}{ruta}", timeout=15, **kw)

    def patch(self, ruta, **kw):
        return self.s.patch(f"{API}{ruta}", timeout=15, **kw)

    def put(self, ruta, **kw):
        return self.s.put(f"{API}{ruta}", timeout=15, **kw)

    def reservar(self, cupo_id: int, clave: str | None = None, **extra):
        return self.post("/citas", json={"cupo_id": cupo_id, **extra},
                         headers={"Idempotency-Key": clave or str(uuid.uuid4())})


@pytest.fixture(scope="session")
def admin():
    return Cliente("admin")


@pytest.fixture(scope="session")
def recepcion():
    return Cliente("recepcion")


@pytest.fixture(scope="session")
def paciente1():
    return Cliente("paciente1")


@pytest.fixture(scope="session")
def paciente2():
    return Cliente("paciente2")


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
