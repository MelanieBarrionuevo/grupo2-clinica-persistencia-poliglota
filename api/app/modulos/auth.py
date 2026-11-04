"""Identidad de la demo: login con usuarios semilla (PostgreSQL) y emisión de JWT."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..db import pg
from ..errores import ApiError
from ..seguridad import Usuario, emitir_token, usuario_actual, verificar_password

router = APIRouter(prefix="/auth", tags=["Identidad"])


class LoginIn(BaseModel):
    usuario: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=1, max_length=100)


@router.post("/login")
def login(d: LoginIn):
    with pg() as conn:
        fila = conn.execute(
            "SELECT id, nombre, rol, password_hash FROM usuario WHERE usuario=%s AND activo", (d.usuario.strip().lower(),)
        ).fetchone()
    if fila is None or not verificar_password(d.password, fila["password_hash"]):
        raise ApiError(401, "CREDENCIALES_INVALIDAS", "Usuario o contraseña incorrectos")
    u = Usuario(id=fila["id"], nombre=fila["nombre"], rol=fila["rol"])
    return {"token": emitir_token(u), "usuario": {"id": u.id, "nombre": u.nombre, "rol": u.rol}}


@router.get("/yo")
def yo(u: Usuario = Depends(usuario_actual)):
    return {"id": u.id, "nombre": u.nombre, "rol": u.rol}
