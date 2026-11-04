"""Módulo Agenda y cupos · PostgreSQL (fuente oficial, consistencia fuerte).

- Publicar franjas: la duración sale del servicio en el catálogo, no del navegador (RN-10).
- Sin solapes por profesional: restricción EXCLUDE USING gist (RN-09) → 409.
- Bloqueo temporal (hold, RN-07): el cupo queda BLOQUEADO para un usuario hasta `bloqueado_hasta`.
  Un bloqueo vencido se trata como DISPONIBLE en todas las consultas y en la reserva,
  y se limpia de forma perezosa al listar (no depende de un reloj externo).
"""
from datetime import date, datetime, time, timedelta, timezone

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel, Field

from ..config import config
from ..db import pg
from ..errores import ApiError
from ..seguridad import Usuario, requiere_rol, usuario_actual, usuario_opcional
from . import catalogo as cat

router = APIRouter(tags=["Agenda y cupos (PostgreSQL)"])
personal = requiere_rol("RECEPCIONISTA", "ADMINISTRADOR")

ESTADO_EFECTIVO = (
    "CASE WHEN c.estado = 'BLOQUEADO' AND c.bloqueado_hasta < now() THEN 'DISPONIBLE' ELSE c.estado END"
)
# Condición SQL reutilizable: el cupo puede ser ocupado por el actor %(actor)s.
CUPO_TOMABLE = (
    "(estado = 'DISPONIBLE' OR (estado = 'BLOQUEADO' AND "
    "(bloqueado_hasta < now() OR bloqueado_por = %(actor)s)))"
)


def liberar_bloqueos_vencidos(conn) -> int:
    with conn.transaction():
        cur = conn.execute(
            "UPDATE cupo SET estado='DISPONIBLE', bloqueado_hasta=NULL, bloqueado_por=NULL, actualizado_en=now() "
            "WHERE estado='BLOQUEADO' AND bloqueado_hasta < now()"
        )
        return cur.rowcount


def salida_cupo(r: dict, u: Usuario | None = None) -> dict:
    return {
        "cupo_id": r["id"],
        "profesional_id": r["profesional_id"],
        "sede_id": r["sede_id"],
        "servicio_id": r["servicio_id"],
        "inicio": r["inicio"],
        "fin": r["fin"],
        "estado": r["estado_efectivo"],
        "bloqueado_hasta": r["bloqueado_hasta"] if r["estado_efectivo"] == "BLOQUEADO" else None,
        "bloqueado_por_mi": bool(u and r.get("bloqueado_por") == u.id and r["estado_efectivo"] == "BLOQUEADO"),
    }


def diagnosticar_cupo(conn, cupo_id: int) -> ApiError:
    """Explica por qué un cupo no se pudo tomar (se llama tras un UPDATE que afectó 0 filas)."""
    r = conn.execute(f"SELECT c.*, {ESTADO_EFECTIVO} AS estado_efectivo FROM cupo c WHERE id=%s", (cupo_id,)).fetchone()
    if r is None:
        return ApiError(404, "CUPO_NO_ENCONTRADO", "La franja no existe")
    if r["inicio"] <= datetime.now(timezone.utc):
        return ApiError(409, "CUPO_PASADO", "La franja ya pasó; no se puede reservar")
    if r["estado_efectivo"] == "BLOQUEADO":
        return ApiError(409, "CUPO_BLOQUEADO", "Otra persona está completando la reserva de esta franja. Pruebe otra o espere unos minutos.")
    return ApiError(409, "CUPO_NO_DISPONIBLE", "La franja ya no está disponible")


# ------------------------------------------------------------------ modelos
class PublicarCupos(BaseModel):
    profesional_id: str = Field(min_length=1)
    sede_id: str = Field(min_length=1)
    servicio_id: str = Field(min_length=1)
    inicio: AwareDatetime = Field(description="Fecha y hora con zona, ej. 2026-11-10T09:00:00-05:00")
    cantidad: int = Field(default=1, ge=1, le=24, description="Franjas consecutivas a publicar")


# ------------------------------------------------------------------ endpoints
@router.get("/cupos")
def listar_cupos(
    profesional_id: str | None = None,
    sede_id: str | None = None,
    servicio_id: str | None = None,
    desde: AwareDatetime | None = None,
    hasta: AwareDatetime | None = None,
    solo_disponibles: bool = False,
    u: Usuario | None = Depends(usuario_opcional),
):
    """Franjas leídas SIEMPRE de PostgreSQL (nunca de Typesense ni Redis)."""
    ahora = datetime.now(timezone.utc)
    desde = desde or ahora
    hasta = hasta or (desde + timedelta(days=14))
    if hasta <= desde or (hasta - desde) > timedelta(days=62):
        raise ApiError(422, "RANGO_INVALIDO", "El rango debe ser positivo y de máximo 62 días")
    condiciones = ["c.inicio >= %(desde)s", "c.inicio < %(hasta)s"]
    params: dict = {"desde": desde, "hasta": hasta}
    for campo, valor in (("profesional_id", profesional_id), ("sede_id", sede_id), ("servicio_id", servicio_id)):
        if valor:
            condiciones.append(f"c.{campo} = %({campo})s")
            params[campo] = valor
    if solo_disponibles:
        condiciones.append(f"{ESTADO_EFECTIVO} = 'DISPONIBLE'")
    with pg() as conn:
        liberar_bloqueos_vencidos(conn)
        filas = conn.execute(
            f"SELECT c.*, {ESTADO_EFECTIVO} AS estado_efectivo FROM cupo c WHERE {' AND '.join(condiciones)} "
            "ORDER BY c.inicio LIMIT 500",
            params,
        ).fetchall()
    return {"origen": "postgresql", "cupos": [salida_cupo(f, u) for f in filas]}


@router.post("/cupos", status_code=201)
def publicar_cupos(d: PublicarCupos, u: Usuario = Depends(personal)):
    datos = cat.validar_publicacion(d.profesional_id, d.servicio_id, d.sede_id)
    duracion = timedelta(minutes=int(datos["servicio"]["duracion_minutos"]))
    if d.inicio <= datetime.now(timezone.utc):
        raise ApiError(422, "INICIO_PASADO", "La franja debe comenzar en el futuro")
    creados = []
    try:
        with pg() as conn, conn.transaction():
            for i in range(d.cantidad):
                inicio = d.inicio + i * duracion
                r = conn.execute(
                    "INSERT INTO cupo (profesional_id, sede_id, servicio_id, inicio, fin) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING *, estado AS estado_efectivo",
                    (d.profesional_id, d.sede_id, d.servicio_id, inicio, inicio + duracion),
                ).fetchone()
                creados.append(salida_cupo(r))
    except psycopg.errors.ExclusionViolation:
        raise ApiError(409, "CUPO_SOLAPADO",
                       "Alguna franja se solapa con otra del mismo profesional. No se publicó ninguna.")
    return {"cupos": creados, "duracion_minutos": int(duracion.total_seconds() // 60)}


@router.post("/cupos/{cupo_id}/bloquear")
def bloquear_cupo(cupo_id: int, u: Usuario = Depends(usuario_actual)):
    """Hold temporal mientras el usuario completa el formulario de reserva (RN-07).

    Un usuario mantiene como máximo un bloqueo: al bloquear otro, se libera el anterior.
    """
    with pg() as conn:
        with conn.transaction():
            conn.execute(
                "UPDATE cupo SET estado='DISPONIBLE', bloqueado_hasta=NULL, bloqueado_por=NULL, actualizado_en=now() "
                "WHERE estado='BLOQUEADO' AND bloqueado_por=%s AND id<>%s",
                (u.id, cupo_id),
            )
            r = conn.execute(
                "UPDATE cupo SET estado='BLOQUEADO', bloqueado_por=%(actor)s, "
                "bloqueado_hasta=now() + make_interval(secs => %(ttl)s), actualizado_en=now() "
                f"WHERE id=%(id)s AND inicio > now() AND {CUPO_TOMABLE} "
                "RETURNING *, estado AS estado_efectivo",
                {"actor": u.id, "ttl": config.ttl_bloqueo_cupo, "id": cupo_id},
            ).fetchone()
        if r is None:
            raise diagnosticar_cupo(conn, cupo_id)
    return salida_cupo(r, u) | {"ttl_segundos": config.ttl_bloqueo_cupo}


@router.post("/cupos/{cupo_id}/liberar")
def liberar_cupo(cupo_id: int, u: Usuario = Depends(usuario_actual)):
    with pg() as conn, conn.transaction():
        r = conn.execute("SELECT * FROM cupo WHERE id=%s FOR UPDATE", (cupo_id,)).fetchone()
        if r is None:
            raise ApiError(404, "CUPO_NO_ENCONTRADO", "La franja no existe")
        if r["estado"] != "BLOQUEADO":
            raise ApiError(409, "CUPO_NO_BLOQUEADO", "La franja no está bloqueada")
        if r["bloqueado_por"] != u.id and not u.es_personal:
            raise ApiError(403, "SIN_PERMISO", "Solo quien bloqueó la franja o recepción pueden liberarla")
        r = conn.execute(
            "UPDATE cupo SET estado='DISPONIBLE', bloqueado_hasta=NULL, bloqueado_por=NULL, actualizado_en=now() "
            "WHERE id=%s RETURNING *, estado AS estado_efectivo",
            (cupo_id,),
        ).fetchone()
    return salida_cupo(r, u)


@router.get("/agenda")
def agenda(
    fecha: date = Query(description="Día local de la clínica, YYYY-MM-DD"),
    profesional_id: str | None = None,
    sede_id: str | None = None,
    u: Usuario = Depends(personal),
):
    """Vista de recepción: franjas del día con su cita activa (si existe)."""
    inicio_dia = datetime.combine(fecha, time.min, tzinfo=config.zona_horaria)
    params: dict = {"desde": inicio_dia, "hasta": inicio_dia + timedelta(days=1)}
    condiciones = ["c.inicio >= %(desde)s", "c.inicio < %(hasta)s"]
    if profesional_id:
        condiciones.append("c.profesional_id = %(profesional_id)s")
        params["profesional_id"] = profesional_id
    if sede_id:
        condiciones.append("c.sede_id = %(sede_id)s")
        params["sede_id"] = sede_id
    with pg() as conn:
        liberar_bloqueos_vencidos(conn)
        filas = conn.execute(
            f"SELECT c.*, {ESTADO_EFECTIVO} AS estado_efectivo, "
            "ci.id AS cita_id, ci.estado AS cita_estado, ci.paciente_id, us.nombre AS paciente "
            "FROM cupo c "
            "LEFT JOIN cita ci ON ci.cupo_id = c.id AND ci.estado IN ('PENDIENTE','CONFIRMADA') "
            "LEFT JOIN usuario us ON us.id = ci.paciente_id "
            f"WHERE {' AND '.join(condiciones)} ORDER BY c.inicio, c.profesional_id",
            params,
        ).fetchall()
    try:
        prof = cat.nombres("profesionales", [f["profesional_id"] for f in filas])
        sedes = cat.nombres("sedes", [f["sede_id"] for f in filas])
        serv = cat.nombres("servicios", [f["servicio_id"] for f in filas])
    except Exception:
        prof, sedes, serv = {}, {}, {}
    salida = []
    for f in filas:
        c = salida_cupo(f, u)
        c.update({
            "profesional": prof.get(f["profesional_id"]),
            "sede": sedes.get(f["sede_id"]),
            "servicio": serv.get(f["servicio_id"]),
            "cita": None if f["cita_id"] is None else {
                "cita_id": f["cita_id"], "estado": f["cita_estado"],
                "paciente_id": f["paciente_id"], "paciente": f["paciente"],
            },
        })
        salida.append(c)
    return {"fecha": fecha, "zona_horaria": str(config.zona_horaria), "cupos": salida}
