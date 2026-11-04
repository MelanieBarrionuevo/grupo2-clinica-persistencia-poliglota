"""Módulo Sesiones y preferencias · Redis (efímero, con TTL).

Guarda filtros de búsqueda y el borrador de reserva del usuario autenticado.
- La clave se deriva del usuario del token: no se puede leer la sesión de otro.
- No es fuente de verdad: el borrador NO ocupa ni bloquea el cupo.
- Si Redis cae → 503 ESTADO_TEMPORAL_NO_DISPONIBLE; reservar sigue funcionando (RN-13).
"""
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..config import config
from ..db import temporal
from ..seguridad import Usuario, usuario_actual

router = APIRouter(prefix="/sesiones", tags=["Sesiones y preferencias (Redis)"])


class Filtros(BaseModel):
    texto: str = Field(default="", max_length=80)
    especialidad_id: str | None = Field(default=None, max_length=60)
    sede_id: str | None = Field(default=None, max_length=60)
    modalidad: str | None = Field(default=None, max_length=20)
    ordenar: str | None = Field(default=None, max_length=20)


class Borrador(BaseModel):
    profesional_id: str = Field(min_length=1, max_length=60)
    servicio_id: str | None = Field(default=None, max_length=60)
    cupo_id: int | None = Field(default=None, ge=1)
    nota: str = Field(default="", max_length=200)


def _guardar(clave: str, datos: dict) -> dict:
    r = temporal()
    r.set(clave, json.dumps(datos), ex=config.ttl_borrador)
    return {"guardado": True, "ttl_segundos": config.ttl_borrador}


def _leer(clave: str) -> dict:
    r = temporal()
    valor = r.get(clave)
    ttl = r.ttl(clave) if valor is not None else None
    return {"datos": json.loads(valor) if valor else None, "ttl_restante": ttl}


@router.put("/filtros")
def guardar_filtros(f: Filtros, u: Usuario = Depends(usuario_actual)):
    return _guardar(f"sesion:{u.id}:filtros", f.model_dump())


@router.get("/filtros")
def leer_filtros(u: Usuario = Depends(usuario_actual)):
    return _leer(f"sesion:{u.id}:filtros")


@router.put("/borrador")
def guardar_borrador(b: Borrador, u: Usuario = Depends(usuario_actual)):
    return _guardar(f"sesion:{u.id}:borrador", b.model_dump())


@router.get("/borrador")
def leer_borrador(u: Usuario = Depends(usuario_actual)):
    return _leer(f"sesion:{u.id}:borrador")


@router.delete("/borrador")
def borrar_borrador(u: Usuario = Depends(usuario_actual)):
    temporal().delete(f"sesion:{u.id}:borrador")
    return {"eliminado": True}
