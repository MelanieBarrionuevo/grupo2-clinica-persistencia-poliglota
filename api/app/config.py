"""Configuración leída de variables de entorno (ver .env.example). Ningún secreto se expone por la API."""
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo


def _env(nombre: str, defecto: str | None = None) -> str:
    valor = os.getenv(nombre, defecto)
    if valor is None:
        raise RuntimeError(f"Falta la variable de entorno {nombre}")
    return valor


@dataclass(frozen=True)
class Config:
    postgres_dsn: str
    mongo_uri: str
    mongo_db: str
    typesense_host: str
    typesense_port: str
    typesense_api_key: str
    redis_host: str
    redis_port: int
    redis_password: str | None
    jwt_secret: str
    jwt_horas: int
    ttl_borrador: int
    ttl_bloqueo_cupo: int
    sync_intervalo_ms: int
    zona_horaria: ZoneInfo


def cargar() -> Config:
    pg = (
        f"host={_env('POSTGRES_HOST', 'localhost')} port={_env('POSTGRES_PORT', '5432')} "
        f"dbname={_env('POSTGRES_DB', 'clinica')} user={_env('POSTGRES_USER', 'clinica_app')} "
        f"password={_env('POSTGRES_PASSWORD', '')} connect_timeout=3"
    )
    return Config(
        postgres_dsn=os.getenv("POSTGRES_DSN", pg),
        mongo_uri=_env("MONGO_URI", "mongodb://localhost:27017/?replicaSet=rs0&directConnection=true"),
        mongo_db=_env("MONGO_DB", "catalogo"),
        typesense_host=_env("TYPESENSE_HOST", "localhost"),
        typesense_port=_env("TYPESENSE_PORT", "8108"),
        typesense_api_key=_env("TYPESENSE_API_KEY", "dev-typesense"),
        redis_host=_env("REDIS_HOST", "localhost"),
        redis_port=int(_env("REDIS_PORT", "6379")),
        redis_password=os.getenv("REDIS_PASSWORD") or None,
        jwt_secret=_env("JWT_SECRET", "solo-desarrollo"),
        jwt_horas=int(_env("JWT_HORAS", "8")),
        ttl_borrador=int(_env("TTL_BORRADOR_SEGUNDOS", "1800")),
        ttl_bloqueo_cupo=int(_env("TTL_BLOQUEO_CUPO_SEGUNDOS", "300")),
        sync_intervalo_ms=int(_env("SYNC_INTERVALO_MS", "1000")),
        zona_horaria=ZoneInfo(_env("CLINICA_TZ", "America/Guayaquil")),
    )


config = cargar()
