"""Módulo Catálogo · MongoDB (fuente oficial).

Toda mutación escribe la entidad y su evento de outbox en la MISMA transacción MongoDB;
el sincronizador (sync/worker.py) lleva después el cambio a Typesense (consistencia eventual).
Nunca se borra físicamente: desactivar = `activo: false` (RN-11).
"""
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..db import catalogo, transaccion_catalogo
from ..errores import ApiError
from ..seguridad import Usuario, requiere_rol, usuario_opcional

router = APIRouter(tags=["Catálogo (MongoDB)"])
solo_admin = requiere_rol("ADMINISTRADOR")

Modalidad = Literal["PRESENCIAL", "TELECONSULTA"]
COLECCION = {"especialidad": "especialidades", "sede": "sedes", "servicio": "servicios", "profesional": "profesionales"}


# ------------------------------------------------------------------ utilidades
def ahora() -> datetime:
    return datetime.now(timezone.utc)


def normalizar(texto: str) -> str:
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", sin_tildes).strip().lower()


def slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalizar(texto)).strip("-")[:40] or uuid.uuid4().hex[:8]


def _limpiar_lista(valores: list[str]) -> list[str]:
    vistos: list[str] = []
    for v in valores:
        v = v.strip()
        if v and v not in vistos:
            vistos.append(v)
    return vistos


def _registrar_outbox(sesion, entidad_tipo: str, entidad_id: str, version: int, tipo: str) -> None:
    catalogo().outbox.insert_one(
        {
            "tipo": f"{entidad_tipo}.{tipo}",
            "entidad_tipo": entidad_tipo,
            "entidad_id": entidad_id,
            "version": version,
            "creado_en": ahora(),
            "procesado_en": None,
            "intentos": 0,
            "ultimo_error": None,
        },
        session=sesion,
    )


def _crear(entidad_tipo: str, doc: dict) -> dict:
    coleccion = catalogo()[COLECCION[entidad_tipo]]
    doc = {**doc, "activo": doc.get("activo", True), "version": 1, "actualizado_en": ahora()}

    def tx(sesion):
        coleccion.insert_one(doc, session=sesion)
        _registrar_outbox(sesion, entidad_tipo, doc["_id"], 1, "creado")
        return doc

    try:
        return transaccion_catalogo(tx)
    except DuplicateKeyError:
        raise ApiError(409, "DUPLICADO", f"Ya existe un registro de {entidad_tipo} con ese nombre o identificador")


def _actualizar(entidad_tipo: str, entidad_id: str, cambios: dict) -> dict:
    coleccion = catalogo()[COLECCION[entidad_tipo]]
    cambios = {**cambios, "actualizado_en": ahora()}

    def tx(sesion):
        nuevo = coleccion.find_one_and_update(
            {"_id": entidad_id},
            {"$set": cambios, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
            session=sesion,
        )
        if nuevo is None:
            raise ApiError(404, "NO_ENCONTRADO", f"No existe {entidad_tipo} {entidad_id}")
        tipo = "desactivado" if cambios.get("activo") is False else "actualizado"
        _registrar_outbox(sesion, entidad_tipo, entidad_id, nuevo["version"], tipo)
        return nuevo

    try:
        return transaccion_catalogo(tx)
    except DuplicateKeyError:
        raise ApiError(409, "DUPLICADO", "Ya existe otro registro con ese nombre")


def _verificar_activos(coleccion: str, ids: list[str], etiqueta: str) -> None:
    encontrados = {d["_id"] for d in catalogo()[coleccion].find({"_id": {"$in": ids}, "activo": True}, {"_id": 1})}
    faltan = [i for i in ids if i not in encontrados]
    if faltan:
        raise ApiError(422, "REFERENCIA_INVALIDA", f"{etiqueta} inexistente o inactiva: {', '.join(faltan)}")


def nombres(coleccion: str, ids) -> dict[str, str]:
    ids = list(set(ids))
    if not ids:
        return {}
    return {d["_id"]: d["nombre"] for d in catalogo()[coleccion].find({"_id": {"$in": ids}}, {"nombre": 1})}


def _salida_simple(doc: dict) -> dict:
    salida = {k: v for k, v in doc.items() if k not in ("_id", "nombre_normalizado")}
    return {"id": doc["_id"], **salida}


# ------------------------------------------------------------------ modelos
class EspecialidadIn(BaseModel):
    nombre: str = Field(min_length=3, max_length=80)
    descripcion: str = Field(default="", max_length=300)


class EspecialidadPatch(BaseModel):
    nombre: str | None = Field(default=None, min_length=3, max_length=80)
    descripcion: str | None = Field(default=None, max_length=300)
    activo: bool | None = None


class SedeIn(BaseModel):
    nombre: str = Field(min_length=3, max_length=80)
    direccion_ficticia: str = Field(min_length=3, max_length=200)
    latitud: float | None = Field(default=None, ge=-90, le=90)
    longitud: float | None = Field(default=None, ge=-180, le=180)


class SedePatch(BaseModel):
    nombre: str | None = Field(default=None, min_length=3, max_length=80)
    direccion_ficticia: str | None = Field(default=None, min_length=3, max_length=200)
    latitud: float | None = Field(default=None, ge=-90, le=90)
    longitud: float | None = Field(default=None, ge=-180, le=180)
    activo: bool | None = None


class ServicioIn(BaseModel):
    nombre: str = Field(min_length=3, max_length=100)
    especialidad_id: str = Field(min_length=1)
    duracion_minutos: int = Field(ge=5, le=240)
    modalidades: list[Modalidad] = Field(min_length=1)


class ServicioPatch(BaseModel):
    nombre: str | None = Field(default=None, min_length=3, max_length=100)
    especialidad_id: str | None = None
    duracion_minutos: int | None = Field(default=None, ge=5, le=240)
    modalidades: list[Modalidad] | None = Field(default=None, min_length=1)
    activo: bool | None = None


class SedeRef(BaseModel):
    sede_id: str = Field(min_length=1)
    horario_ref: str = Field(default="", max_length=80)


class ProfesionalIn(BaseModel):
    nombre: str = Field(min_length=3, max_length=100)
    especialidad_ids: list[str] = Field(min_length=1)
    sedes: list[SedeRef] = Field(min_length=1)
    idiomas: list[str] = Field(default_factory=lambda: ["es"])
    modalidades: list[Modalidad] = Field(min_length=1)
    certificaciones: list[str] = Field(default_factory=list)
    # Atributos variables propios de cada ficha (ej. "consultorio": "204").
    atributos: dict[str, str] = Field(default_factory=dict)

    @field_validator("atributos")
    @classmethod
    def _atributos(cls, v: dict[str, str]):
        if len(v) > 15:
            raise ValueError("máximo 15 atributos")
        return v


class ProfesionalPatch(BaseModel):
    nombre: str | None = Field(default=None, min_length=3, max_length=100)
    especialidad_ids: list[str] | None = Field(default=None, min_length=1)
    sedes: list[SedeRef] | None = Field(default=None, min_length=1)
    idiomas: list[str] | None = None
    modalidades: list[Modalidad] | None = Field(default=None, min_length=1)
    certificaciones: list[str] | None = None
    atributos: dict[str, str] | None = None
    activo: bool | None = None


# ------------------------------------------------------------------ operaciones (reutilizadas por seed)
def crear_especialidad(d: EspecialidadIn, id_fijo: str | None = None) -> dict:
    return _crear("especialidad", {
        "_id": id_fijo or f"esp-{slug(d.nombre)}",
        "nombre": d.nombre.strip(),
        "nombre_normalizado": normalizar(d.nombre),
        "descripcion": d.descripcion.strip(),
    })


def crear_sede(d: SedeIn, id_fijo: str | None = None) -> dict:
    doc = {
        "_id": id_fijo or f"sede-{slug(d.nombre)}",
        "nombre": d.nombre.strip(),
        "nombre_normalizado": normalizar(d.nombre),
        "direccion_ficticia": d.direccion_ficticia.strip(),
    }
    if d.latitud is not None and d.longitud is not None:
        doc["ubicacion"] = {"type": "Point", "coordinates": [float(d.longitud), float(d.latitud)]}
    return _crear("sede", doc)


def crear_servicio(d: ServicioIn, id_fijo: str | None = None) -> dict:
    _verificar_activos("especialidades", [d.especialidad_id], "Especialidad")
    return _crear("servicio", {
        "_id": id_fijo or f"serv-{uuid.uuid4().hex[:8]}",
        "nombre": d.nombre.strip(),
        "especialidad_id": d.especialidad_id,
        "duracion_minutos": d.duracion_minutos,
        "modalidades": _limpiar_lista(d.modalidades),
    })


def crear_profesional(d: ProfesionalIn, id_fijo: str | None = None) -> dict:
    especialidades = _limpiar_lista(d.especialidad_ids)
    sede_ids = _limpiar_lista([s.sede_id for s in d.sedes])
    _verificar_activos("especialidades", especialidades, "Especialidad")
    _verificar_activos("sedes", sede_ids, "Sede")
    return _crear("profesional", {
        "_id": id_fijo or f"prof-{uuid.uuid4().hex[:8]}",
        "nombre": d.nombre.strip(),
        "especialidad_ids": especialidades,
        "sedes": [{"sede_id": s.sede_id, "horario_ref": s.horario_ref.strip()} for s in d.sedes],
        "idiomas": _limpiar_lista(d.idiomas),
        "modalidades": _limpiar_lista(d.modalidades),
        "certificaciones": _limpiar_lista(d.certificaciones),
        "atributos": {k.strip(): v.strip() for k, v in d.atributos.items() if k.strip()},
    })


def ficha_profesional(doc: dict, incluir_servicios: bool = True) -> dict:
    esp = nombres("especialidades", doc.get("especialidad_ids", []))
    sedes = {s["_id"]: s for s in catalogo().sedes.find({"_id": {"$in": [x["sede_id"] for x in doc.get("sedes", [])]}})}
    salida = {
        "id": doc["_id"],
        "nombre": doc["nombre"],
        "activo": doc.get("activo", True),
        "version": doc.get("version", 1),
        "especialidades": [{"id": e, "nombre": esp.get(e, e)} for e in doc.get("especialidad_ids", [])],
        "sedes": [
            {
                "sede_id": s["sede_id"],
                "nombre": sedes.get(s["sede_id"], {}).get("nombre", s["sede_id"]),
                "direccion_ficticia": sedes.get(s["sede_id"], {}).get("direccion_ficticia", ""),
                "activa": sedes.get(s["sede_id"], {}).get("activo", False),
                "horario_ref": s.get("horario_ref", ""),
            }
            for s in doc.get("sedes", [])
        ],
        "idiomas": doc.get("idiomas", []),
        "modalidades": doc.get("modalidades", []),
        "certificaciones": doc.get("certificaciones", []),
        "atributos": doc.get("atributos", {}),
        "actualizado_en": doc.get("actualizado_en"),
    }
    if incluir_servicios:
        salida["servicios"] = [
            {"id": s["_id"], "nombre": s["nombre"], "duracion_minutos": s["duracion_minutos"],
             "modalidades": s["modalidades"], "especialidad_id": s["especialidad_id"]}
            for s in catalogo().servicios.find(
                {"especialidad_id": {"$in": doc.get("especialidad_ids", [])}, "activo": True}
            ).sort("nombre", 1)
        ]
    return salida


def validar_reservable(profesional_id: str, servicio_id: str, sede_id: str) -> dict:
    """Comprueba en la fuente oficial que profesional, servicio y sede siguen activos (RN-08).

    No es una transacción distribuida con PostgreSQL: existe una ventana mínima si el
    administrador desactiva justo durante la reserva (documentado en Fase 4).
    """
    db = catalogo()
    prof = db.profesionales.find_one({"_id": profesional_id})
    if not prof or not prof.get("activo"):
        raise ApiError(409, "PROFESIONAL_INACTIVO", "El profesional no está disponible para nuevas reservas")
    serv = db.servicios.find_one({"_id": servicio_id})
    if not serv or not serv.get("activo"):
        raise ApiError(409, "SERVICIO_INACTIVO", "El servicio no está disponible para nuevas reservas")
    sede = db.sedes.find_one({"_id": sede_id})
    if not sede or not sede.get("activo"):
        raise ApiError(409, "SEDE_INACTIVA", "La sede no está disponible para nuevas reservas")
    return {"profesional": prof, "servicio": serv, "sede": sede}


def validar_publicacion(profesional_id: str, servicio_id: str, sede_id: str) -> dict:
    datos = validar_reservable(profesional_id, servicio_id, sede_id)
    prof, serv = datos["profesional"], datos["servicio"]
    if sede_id not in [s["sede_id"] for s in prof.get("sedes", [])]:
        raise ApiError(422, "SEDE_NO_ASIGNADA", "El profesional no atiende en esa sede")
    if serv["especialidad_id"] not in prof.get("especialidad_ids", []):
        raise ApiError(422, "SERVICIO_NO_CORRESPONDE", "El servicio no corresponde a una especialidad del profesional")
    return datos


# ------------------------------------------------------------------ endpoints: especialidades
@router.get("/especialidades")
def listar_especialidades(incluir_inactivas: bool = False):
    filtro = {} if incluir_inactivas else {"activo": True}
    return {"especialidades": [_salida_simple(d) for d in catalogo().especialidades.find(filtro).sort("nombre", 1)]}


@router.post("/especialidades", status_code=201)
def post_especialidad(d: EspecialidadIn, _: Usuario = Depends(solo_admin)):
    return _salida_simple(crear_especialidad(d)) | {"sincronizacion": "PENDIENTE"}


@router.patch("/especialidades/{especialidad_id}")
def patch_especialidad(especialidad_id: str, d: EspecialidadPatch, _: Usuario = Depends(solo_admin)):
    cambios = d.model_dump(exclude_none=True)
    if "nombre" in cambios:
        cambios["nombre"] = cambios["nombre"].strip()
        cambios["nombre_normalizado"] = normalizar(cambios["nombre"])
    if not cambios:
        raise ApiError(422, "SIN_CAMBIOS", "No se envió ningún campo para modificar")
    return _salida_simple(_actualizar("especialidad", especialidad_id, cambios)) | {"sincronizacion": "PENDIENTE"}


# ------------------------------------------------------------------ endpoints: sedes
@router.get("/sedes")
def listar_sedes(incluir_inactivas: bool = False):
    filtro = {} if incluir_inactivas else {"activo": True}
    return {"sedes": [_salida_simple(d) for d in catalogo().sedes.find(filtro).sort("nombre", 1)]}


@router.post("/sedes", status_code=201)
def post_sede(d: SedeIn, _: Usuario = Depends(solo_admin)):
    return _salida_simple(crear_sede(d)) | {"sincronizacion": "PENDIENTE"}


@router.patch("/sedes/{sede_id}")
def patch_sede(sede_id: str, d: SedePatch, _: Usuario = Depends(solo_admin)):
    cambios = d.model_dump(exclude_none=True)
    lat, lon = cambios.pop("latitud", None), cambios.pop("longitud", None)
    if lat is not None and lon is not None:
        cambios["ubicacion"] = {"type": "Point", "coordinates": [float(lon), float(lat)]}
    if "nombre" in cambios:
        cambios["nombre"] = cambios["nombre"].strip()
        cambios["nombre_normalizado"] = normalizar(cambios["nombre"])
    if not cambios:
        raise ApiError(422, "SIN_CAMBIOS", "No se envió ningún campo para modificar")
    return _salida_simple(_actualizar("sede", sede_id, cambios)) | {"sincronizacion": "PENDIENTE"}


# ------------------------------------------------------------------ endpoints: servicios
@router.get("/servicios")
def listar_servicios(especialidad_id: str | None = None, incluir_inactivos: bool = False):
    filtro: dict = {} if incluir_inactivos else {"activo": True}
    if especialidad_id:
        filtro["especialidad_id"] = especialidad_id
    return {"servicios": [_salida_simple(d) for d in catalogo().servicios.find(filtro).sort("nombre", 1)]}


@router.post("/servicios", status_code=201)
def post_servicio(d: ServicioIn, _: Usuario = Depends(solo_admin)):
    return _salida_simple(crear_servicio(d)) | {"sincronizacion": "PENDIENTE"}


@router.patch("/servicios/{servicio_id}")
def patch_servicio(servicio_id: str, d: ServicioPatch, _: Usuario = Depends(solo_admin)):
    cambios = d.model_dump(exclude_none=True)
    if "especialidad_id" in cambios:
        _verificar_activos("especialidades", [cambios["especialidad_id"]], "Especialidad")
    if "nombre" in cambios:
        cambios["nombre"] = cambios["nombre"].strip()
    if not cambios:
        raise ApiError(422, "SIN_CAMBIOS", "No se envió ningún campo para modificar")
    return _salida_simple(_actualizar("servicio", servicio_id, cambios)) | {"sincronizacion": "PENDIENTE"}


# ------------------------------------------------------------------ endpoints: profesionales
@router.get("/profesionales")
def listar_profesionales(
    especialidad_id: str | None = None,
    sede_id: str | None = None,
    q: str | None = Query(default=None, max_length=80),
    incluir_inactivos: bool = False,
    u: Usuario | None = Depends(usuario_opcional),
):
    """Listado directo desde la fuente oficial (MongoDB).

    Lo usa administración y la web como **modo degradado** cuando el buscador (Typesense) no responde.
    """
    filtro: dict = {}
    if not (incluir_inactivos and u and u.rol == "ADMINISTRADOR"):
        filtro["activo"] = True
    if especialidad_id:
        filtro["especialidad_ids"] = especialidad_id
    if sede_id:
        filtro["sedes.sede_id"] = sede_id
    if q:
        filtro["nombre"] = {"$regex": re.escape(q.strip()), "$options": "i"}
    docs = list(catalogo().profesionales.find(filtro).sort("nombre", 1).limit(200))
    return {"total": len(docs), "origen": "mongodb", "profesionales": [ficha_profesional(d, False) for d in docs]}


@router.get("/profesionales/{profesional_id}")
def get_profesional(profesional_id: str):
    doc = catalogo().profesionales.find_one({"_id": profesional_id})
    if not doc:
        raise ApiError(404, "NO_ENCONTRADO", "Profesional no encontrado")
    return ficha_profesional(doc)


@router.post("/profesionales", status_code=201)
def post_profesional(d: ProfesionalIn, _: Usuario = Depends(solo_admin)):
    doc = crear_profesional(d)
    return {"id": doc["_id"], "nombre": doc["nombre"], "activo": True, "version": 1, "sincronizacion": "PENDIENTE"}


@router.patch("/profesionales/{profesional_id}")
def patch_profesional(profesional_id: str, d: ProfesionalPatch, _: Usuario = Depends(solo_admin)):
    cambios = d.model_dump(exclude_none=True)
    if "especialidad_ids" in cambios:
        cambios["especialidad_ids"] = _limpiar_lista(cambios["especialidad_ids"])
        _verificar_activos("especialidades", cambios["especialidad_ids"], "Especialidad")
    if "sedes" in cambios:
        _verificar_activos("sedes", _limpiar_lista([s["sede_id"] for s in cambios["sedes"]]), "Sede")
    for campo in ("idiomas", "modalidades", "certificaciones"):
        if campo in cambios:
            cambios[campo] = _limpiar_lista(cambios[campo])
    if "nombre" in cambios:
        cambios["nombre"] = cambios["nombre"].strip()
    if not cambios:
        raise ApiError(422, "SIN_CAMBIOS", "No se envió ningún campo para modificar")
    doc = _actualizar("profesional", profesional_id, cambios)
    return {"id": doc["_id"], "nombre": doc["nombre"], "activo": doc["activo"], "version": doc["version"],
            "sincronizacion": "PENDIENTE"}
