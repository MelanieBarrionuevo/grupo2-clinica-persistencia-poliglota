"""Módulo Citas · PostgreSQL (fuente oficial, consistencia fuerte).

Cada operación es UNA transacción local que actualiza cupo + cita + evento juntos.
- Reservar: UPDATE condicional del cupo (gana una sola transacción) + índice único parcial
  de cita activa por cupo como segunda barrera (RN-01, RN-02).
- Idempotency-Key obligatoria: un reintento idéntico devuelve la misma cita (RN-03).
- Cancelar libera el cupo sin borrar historial (RN-04). Reprogramar es atómico (RN-05).
- Cada cambio registra estado anterior, nuevo, actor y fecha en evento_cita (RN-06).
"""
import uuid

import psycopg
from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, Field

from ..db import pg
from ..errores import ApiError
from ..seguridad import Usuario, requiere_rol, usuario_actual
from . import catalogo as cat
from .agenda import CUPO_TOMABLE, diagnosticar_cupo

router = APIRouter(tags=["Citas (PostgreSQL)"])
personal = requiere_rol("RECEPCIONISTA", "ADMINISTRADOR")

SELECT_CITA = (
    "SELECT ci.*, cu.inicio, cu.fin, cu.profesional_id, cu.sede_id, us.nombre AS paciente "
    "FROM cita ci JOIN cupo cu ON cu.id = ci.cupo_id LEFT JOIN usuario us ON us.id = ci.paciente_id "
)


class ReservaIn(BaseModel):
    cupo_id: int = Field(ge=1)
    servicio_id: str | None = Field(default=None, description="Opcional; si se envía debe coincidir con el del cupo")
    paciente_id: str | None = Field(default=None, description="Solo recepción: paciente para quien reserva")


class ReprogramarIn(BaseModel):
    nuevo_cupo_id: int = Field(ge=1)


# ------------------------------------------------------------------ utilidades
def _enriquecer(filas: list[dict], u: Usuario) -> list[dict]:
    """Agrega nombres del catálogo (MongoDB). Si el catálogo cae, se devuelven solo los IDs."""
    try:
        prof = cat.nombres("profesionales", [f["profesional_id"] for f in filas])
        sedes = cat.nombres("sedes", [f["sede_id"] for f in filas])
        serv = cat.nombres("servicios", [f["servicio_id"] for f in filas])
    except Exception:
        prof, sedes, serv = {}, {}, {}
    salida = []
    for f in filas:
        item = {
            "cita_id": str(f["id"]),
            "estado": f["estado"],
            "cupo_id": f["cupo_id"],
            "inicio": f["inicio"],
            "fin": f["fin"],
            "profesional_id": f["profesional_id"],
            "profesional": prof.get(f["profesional_id"]),
            "sede_id": f["sede_id"],
            "sede": sedes.get(f["sede_id"]),
            "servicio_id": f["servicio_id"],
            "servicio": serv.get(f["servicio_id"]),
            "creada_en": f["creada_en"],
            "actualizada_en": f["actualizada_en"],
        }
        if u.es_personal:
            item["paciente_id"] = f["paciente_id"]
            item["paciente"] = f.get("paciente")
        salida.append(item)
    return salida


def _cargar(conn, cita_id, u: Usuario) -> dict:
    fila = conn.execute(SELECT_CITA + "WHERE ci.id = %s", (cita_id,)).fetchone()
    return _enriquecer([fila], u)[0]


def _insertar_evento(conn, cita_id, tipo, anterior, nuevo, u: Usuario, cupo_anterior=None, cupo_nuevo=None):
    conn.execute(
        "INSERT INTO evento_cita (cita_id, tipo, estado_anterior, estado_nuevo, cupo_anterior_id, cupo_nuevo_id, "
        "actor_id, actor_rol) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (cita_id, tipo, anterior, nuevo, cupo_anterior, cupo_nuevo, u.id, u.rol),
    )


def _uuid(valor: str, campo: str) -> uuid.UUID:
    try:
        return uuid.UUID(valor)
    except (ValueError, TypeError):
        raise ApiError(422, "VALIDACION", f"{campo} debe ser un UUID válido")


def _bloquear_cita(conn, cita_id: uuid.UUID, u: Usuario) -> dict:
    c = conn.execute(
        "SELECT ci.*, cu.inicio FROM cita ci JOIN cupo cu ON cu.id = ci.cupo_id WHERE ci.id = %s FOR UPDATE OF ci",
        (cita_id,),
    ).fetchone()
    if c is None:
        raise ApiError(404, "CITA_NO_ENCONTRADA", "La cita no existe")
    if u.rol == "PACIENTE" and c["paciente_id"] != u.id:
        raise ApiError(403, "SIN_PERMISO", "Solo puede gestionar sus propias citas")
    return c


def _respuesta_reintento(conn, previa: dict, paciente_id: str, cupo_id: int, u: Usuario) -> dict:
    """Misma clave: si es la misma solicitud del mismo paciente, devolver la misma cita (200)."""
    original = conn.execute(
        "SELECT cupo_nuevo_id FROM evento_cita WHERE cita_id=%s AND tipo='CREADA' ORDER BY id LIMIT 1",
        (previa["id"],),
    ).fetchone()
    cupo_original = original["cupo_nuevo_id"] if original else previa["cupo_id"]
    if previa["paciente_id"] != paciente_id or cupo_original != cupo_id:
        # No se revela nada de la otra cita.
        raise ApiError(409, "CLAVE_IDEMPOTENCIA_REUTILIZADA",
                       "La clave de idempotencia ya se usó para otra solicitud. Genere una nueva.")
    return _cargar(conn, previa["id"], u) | {"reintento": True}


# ------------------------------------------------------------------ reservar
@router.post("/citas", status_code=201, responses={200: {"description": "Reintento idempotente: misma cita"}})
def reservar(
    d: ReservaIn,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", description="UUID generado por el cliente"),
    u: Usuario = Depends(requiere_rol("PACIENTE", "RECEPCIONISTA")),
):
    clave = _uuid(idempotency_key, "Idempotency-Key")

    # 1. Identidad: el paciente sale del token; recepción indica para quién reserva.
    if u.rol == "PACIENTE":
        if d.paciente_id and d.paciente_id != u.id:
            raise ApiError(403, "SIN_PERMISO", "Un paciente solo puede reservar para sí mismo")
        paciente_id, estado_inicial = u.id, "PENDIENTE"
    else:
        if not d.paciente_id:
            raise ApiError(422, "PACIENTE_REQUERIDO", "Recepción debe indicar el paciente")
        paciente_id, estado_inicial = d.paciente_id, "CONFIRMADA"

    with pg() as conn:
        if u.rol != "PACIENTE":
            p = conn.execute("SELECT 1 FROM usuario WHERE id=%s AND rol='PACIENTE' AND activo", (paciente_id,)).fetchone()
            if p is None:
                raise ApiError(422, "PACIENTE_INVALIDO", "El paciente no existe")
        previa = conn.execute("SELECT * FROM cita WHERE clave_idempotencia=%s", (clave,)).fetchone()
        if previa:
            response.status_code = 200
            return _respuesta_reintento(conn, previa, paciente_id, d.cupo_id, u)
        cupo = conn.execute("SELECT * FROM cupo WHERE id=%s", (d.cupo_id,)).fetchone()
        if cupo is None:
            raise ApiError(404, "CUPO_NO_ENCONTRADO", "La franja no existe")
        if d.servicio_id and d.servicio_id != cupo["servicio_id"]:
            raise ApiError(422, "SERVICIO_NO_COINCIDE", "El servicio no corresponde a la franja elegida")

    # 2. Catálogo oficial (MongoDB): profesional, servicio y sede activos. Fuera de la
    #    transacción de PostgreSQL para no retener bloqueos durante una llamada de red.
    cat.validar_reservable(cupo["profesional_id"], cupo["servicio_id"], cupo["sede_id"])

    # 3. Transacción local en PostgreSQL: ocupar cupo + crear cita + evento.
    cita_id = uuid.uuid4()
    try:
        with pg() as conn:
            with conn.transaction():
                tomado = conn.execute(
                    "UPDATE cupo SET estado='RESERVADO', bloqueado_hasta=NULL, bloqueado_por=NULL, actualizado_en=now() "
                    f"WHERE id=%(id)s AND inicio > now() AND {CUPO_TOMABLE} RETURNING id",
                    {"id": d.cupo_id, "actor": u.id if u.rol == "RECEPCIONISTA" else paciente_id},
                ).fetchone()
                if tomado is not None:
                    conn.execute(
                        "INSERT INTO cita (id, cupo_id, paciente_id, servicio_id, estado, clave_idempotencia) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (cita_id, d.cupo_id, paciente_id, cupo["servicio_id"], estado_inicial, clave),
                    )
                    _insertar_evento(conn, cita_id, "CREADA", None, estado_inicial, u, cupo_nuevo=d.cupo_id)
            if tomado is None:
                # ¿Era un reintento concurrente con la misma clave que ya ganó?
                previa = conn.execute("SELECT * FROM cita WHERE clave_idempotencia=%s", (clave,)).fetchone()
                if previa:
                    response.status_code = 200
                    return _respuesta_reintento(conn, previa, paciente_id, d.cupo_id, u)
                raise diagnosticar_cupo(conn, d.cupo_id)
            return _cargar(conn, cita_id, u)
    except psycopg.errors.UniqueViolation as exc:
        restriccion = getattr(exc.diag, "constraint_name", "") or ""
        if "clave_idempotencia" in restriccion:
            with pg() as conn:
                previa = conn.execute("SELECT * FROM cita WHERE clave_idempotencia=%s", (clave,)).fetchone()
                response.status_code = 200
                return _respuesta_reintento(conn, previa, paciente_id, d.cupo_id, u)
        raise ApiError(409, "CUPO_NO_DISPONIBLE", "La franja ya no está disponible")


# ------------------------------------------------------------------ consultas
@router.get("/citas/mias")
def mis_citas(u: Usuario = Depends(requiere_rol("PACIENTE"))):
    with pg() as conn:
        filas = conn.execute(SELECT_CITA + "WHERE ci.paciente_id = %s ORDER BY cu.inicio DESC", (u.id,)).fetchall()
    return {"citas": _enriquecer(filas, u)}


@router.get("/citas")
def listar_citas(
    estado: str | None = Query(default=None, pattern="^(PENDIENTE|CONFIRMADA|CANCELADA)$"),
    profesional_id: str | None = None,
    limite: int = Query(default=100, ge=1, le=500),
    u: Usuario = Depends(personal),
):
    condiciones, params = ["TRUE"], []
    if estado:
        condiciones.append("ci.estado = %s")
        params.append(estado)
    if profesional_id:
        condiciones.append("cu.profesional_id = %s")
        params.append(profesional_id)
    params.append(limite)
    with pg() as conn:
        filas = conn.execute(
            SELECT_CITA + f"WHERE {' AND '.join(condiciones)} ORDER BY cu.inicio DESC LIMIT %s", params
        ).fetchall()
    return {"citas": _enriquecer(filas, u)}


@router.get("/citas/{cita_id}")
def detalle_cita(cita_id: str, u: Usuario = Depends(usuario_actual)):
    cid = _uuid(cita_id, "cita_id")
    with pg() as conn:
        fila = conn.execute(SELECT_CITA + "WHERE ci.id = %s", (cid,)).fetchone()
        if fila is None:
            raise ApiError(404, "CITA_NO_ENCONTRADA", "La cita no existe")
        if u.rol == "PACIENTE" and fila["paciente_id"] != u.id:
            raise ApiError(403, "SIN_PERMISO", "Solo puede ver sus propias citas")
        eventos = conn.execute(
            "SELECT tipo, estado_anterior, estado_nuevo, cupo_anterior_id, cupo_nuevo_id, actor_rol, "
            "CASE WHEN actor_rol = 'PACIENTE' THEN 'Paciente' ELSE actor_id END AS actor, ocurrido_en "
            "FROM evento_cita WHERE cita_id = %s ORDER BY id",
            (cid,),
        ).fetchall()
    return _enriquecer([fila], u)[0] | {"eventos": eventos}


# ------------------------------------------------------------------ transiciones
@router.patch("/citas/{cita_id}/confirmar")
def confirmar(cita_id: str, u: Usuario = Depends(personal)):
    cid = _uuid(cita_id, "cita_id")
    with pg() as conn:
        with conn.transaction():
            c = _bloquear_cita(conn, cid, u)
            if c["estado"] != "PENDIENTE":
                raise ApiError(409, "TRANSICION_INVALIDA", f"No se puede confirmar una cita {c['estado']}")
            conn.execute("UPDATE cita SET estado='CONFIRMADA', actualizada_en=now() WHERE id=%s", (cid,))
            _insertar_evento(conn, cid, "CONFIRMADA", "PENDIENTE", "CONFIRMADA", u)
        return _cargar(conn, cid, u)


@router.patch("/citas/{cita_id}/cancelar")
def cancelar(cita_id: str, u: Usuario = Depends(requiere_rol("PACIENTE", "RECEPCIONISTA"))):
    cid = _uuid(cita_id, "cita_id")
    with pg() as conn:
        with conn.transaction():
            c = _bloquear_cita(conn, cid, u)
            if c["estado"] == "CANCELADA":
                raise ApiError(409, "TRANSICION_INVALIDA", "La cita ya está cancelada")
            if u.rol == "PACIENTE" and c["inicio"] <= _ahora(conn):
                raise ApiError(409, "CITA_PASADA", "No se puede cancelar una cita que ya ocurrió")
            conn.execute("UPDATE cita SET estado='CANCELADA', actualizada_en=now() WHERE id=%s", (cid,))
            conn.execute(
                "UPDATE cupo SET estado='DISPONIBLE', bloqueado_hasta=NULL, bloqueado_por=NULL, actualizado_en=now() "
                "WHERE id=%s",
                (c["cupo_id"],),
            )
            _insertar_evento(conn, cid, "CANCELADA", c["estado"], "CANCELADA", u, cupo_anterior=c["cupo_id"])
        return _cargar(conn, cid, u) | {"cupo_liberado": True}


@router.patch("/citas/{cita_id}/reprogramar")
def reprogramar(cita_id: str, d: ReprogramarIn, u: Usuario = Depends(requiere_rol("PACIENTE", "RECEPCIONISTA"))):
    """Mueve la cita a otra franja del mismo servicio en una sola transacción.

    Regla de estado (decisión Fase 4): si reprograma recepción se conserva el estado;
    si reprograma el paciente vuelve a PENDIENTE para que recepción la confirme de nuevo.
    """
    cid = _uuid(cita_id, "cita_id")
    with pg() as conn:
        destino = conn.execute("SELECT * FROM cupo WHERE id=%s", (d.nuevo_cupo_id,)).fetchone()
    if destino is None:
        raise ApiError(404, "CUPO_NO_ENCONTRADO", "La franja destino no existe")
    cat.validar_reservable(destino["profesional_id"], destino["servicio_id"], destino["sede_id"])

    with pg() as conn:
        with conn.transaction():
            c = _bloquear_cita(conn, cid, u)
            if c["estado"] == "CANCELADA":
                raise ApiError(409, "TRANSICION_INVALIDA", "No se puede reprogramar una cita cancelada")
            if c["cupo_id"] == d.nuevo_cupo_id:
                raise ApiError(409, "MISMO_CUPO", "La cita ya está en esa franja")
            if c["inicio"] <= _ahora(conn):
                raise ApiError(409, "CITA_PASADA", "No se puede reprogramar una cita que ya ocurrió")
            # Bloqueo de ambos cupos en orden fijo (id ascendente) para evitar interbloqueos.
            cupos = {
                r["id"]: r for r in conn.execute(
                    "SELECT * FROM cupo WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
                    ([c["cupo_id"], d.nuevo_cupo_id],),
                ).fetchall()
            }
            nuevo = cupos[d.nuevo_cupo_id]
            if nuevo["servicio_id"] != c["servicio_id"]:
                raise ApiError(422, "SERVICIO_NO_COINCIDE", "La nueva franja debe ser del mismo servicio")
            tomado = conn.execute(
                "UPDATE cupo SET estado='RESERVADO', bloqueado_hasta=NULL, bloqueado_por=NULL, actualizado_en=now() "
                f"WHERE id=%(id)s AND inicio > now() AND {CUPO_TOMABLE} RETURNING id",
                {"id": d.nuevo_cupo_id, "actor": u.id},
            ).fetchone()
            if tomado is None:
                raise diagnosticar_cupo(conn, d.nuevo_cupo_id)  # rollback: nada cambia
            conn.execute(
                "UPDATE cupo SET estado='DISPONIBLE', bloqueado_hasta=NULL, bloqueado_por=NULL, actualizado_en=now() "
                "WHERE id=%s",
                (c["cupo_id"],),
            )
            estado_nuevo = c["estado"] if u.es_personal else "PENDIENTE"
            conn.execute(
                "UPDATE cita SET cupo_id=%s, estado=%s, actualizada_en=now() WHERE id=%s",
                (d.nuevo_cupo_id, estado_nuevo, cid),
            )
            _insertar_evento(conn, cid, "REPROGRAMADA", c["estado"], estado_nuevo, u,
                             cupo_anterior=c["cupo_id"], cupo_nuevo=d.nuevo_cupo_id)
        return _cargar(conn, cid, u) | {"cupo_anterior_id": c["cupo_id"], "cupo_nuevo_id": d.nuevo_cupo_id}


def _ahora(conn):
    return conn.execute("SELECT now() AS t").fetchone()["t"]


# ------------------------------------------------------------------ pacientes (para recepción)
@router.get("/pacientes", tags=["Identidad"])
def pacientes(u: Usuario = Depends(personal)):
    with pg() as conn:
        filas = conn.execute("SELECT id, nombre FROM usuario WHERE rol='PACIENTE' AND activo ORDER BY nombre").fetchall()
    return {"pacientes": filas}
