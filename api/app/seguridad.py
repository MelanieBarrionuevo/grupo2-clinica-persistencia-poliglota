"""Identidad mínima de la demo: usuarios semilla en PostgreSQL + JWT firmado por la API.

El rol y el identificador del paciente salen SIEMPRE del token verificado, nunca del cuerpo
de la petición (RS-04, RN-14).
"""
import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import config
from .errores import ApiError

ROLES = ("PACIENTE", "RECEPCIONISTA", "ADMINISTRADOR")
_ITERACIONES = 200_000

_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    sal = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), sal, _ITERACIONES)
    return f"pbkdf2_sha256${_ITERACIONES}${base64.b64encode(sal).decode()}${base64.b64encode(dk).decode()}"


def verificar_password(password: str, almacenado: str) -> bool:
    try:
        _, iteraciones, sal_b64, dk_b64 = almacenado.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(sal_b64), int(iteraciones))
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class Usuario:
    id: str
    nombre: str
    rol: str

    @property
    def es_personal(self) -> bool:
        return self.rol in ("RECEPCIONISTA", "ADMINISTRADOR")


def emitir_token(u: Usuario) -> str:
    ahora = datetime.now(timezone.utc)
    payload = {
        "sub": u.id,
        "nombre": u.nombre,
        "rol": u.rol,
        "iat": ahora,
        "exp": ahora + timedelta(hours=config.jwt_horas),
    }
    return jwt.encode(payload, config.jwt_secret, algorithm="HS256")


def _decodificar(token: str) -> Usuario:
    try:
        datos = jwt.decode(token, config.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "SESION_EXPIRADA", "La sesión expiró; vuelva a iniciar sesión")
    except jwt.InvalidTokenError:
        raise ApiError(401, "TOKEN_INVALIDO", "Token inválido")
    if datos.get("rol") not in ROLES:
        raise ApiError(401, "TOKEN_INVALIDO", "Token inválido")
    return Usuario(id=datos["sub"], nombre=datos.get("nombre", ""), rol=datos["rol"])


def usuario_opcional(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Usuario | None:
    if cred is None:
        return None
    return _decodificar(cred.credentials)


def usuario_actual(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Usuario:
    if cred is None:
        raise ApiError(401, "NO_AUTENTICADO", "Debe iniciar sesión")
    return _decodificar(cred.credentials)


def requiere_rol(*roles: str):
    def _dep(u: Usuario = Depends(usuario_actual)) -> Usuario:
        if u.rol not in roles:
            raise ApiError(403, "SIN_PERMISO", "Su rol no permite esta operación")
        return u
    return _dep
