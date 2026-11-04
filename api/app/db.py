"""Clientes de los cuatro gestores. Cada módulo de dominio usa solo el que le corresponde.

- PostgreSQL: agenda, cupos, citas, historial y cuentas de demo (fuente oficial, transacciones locales).
- MongoDB: catálogo oficial + outbox (transacción multi-documento en el replica set rs0).
- Typesense: proyección de búsqueda (derivada).
- Redis: filtros y borradores con TTL (efímero).
"""
from contextlib import contextmanager
from typing import Callable, TypeVar

import redis
import typesense
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pymongo import MongoClient
from pymongo.database import Database

from .config import config

T = TypeVar("T")

# ---------------- PostgreSQL ----------------
_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            config.postgres_dsn,
            min_size=1,
            max_size=10,
            timeout=5,
            # Verifica la conexión antes de entregarla: tras reiniciar PostgreSQL no se
            # reutilizan conexiones rotas y la API se recupera sola.
            check=ConnectionPool.check_connection,
            kwargs={"row_factory": dict_row, "autocommit": False},
            open=False,
        )
        _pool.open(wait=False)
    return _pool


@contextmanager
def pg():
    """Conexión de PostgreSQL. Usar `with conn.transaction():` para delimitar la transacción local."""
    with pool().connection() as conn:
        yield conn


# ---------------- MongoDB ----------------
_mongo: MongoClient | None = None


def mongo_cliente() -> MongoClient:
    global _mongo
    if _mongo is None:
        _mongo = MongoClient(
            config.mongo_uri,
            serverSelectionTimeoutMS=3000,
            connectTimeoutMS=3000,
            tz_aware=True,
        )
    return _mongo


def catalogo() -> Database:
    return mongo_cliente()[config.mongo_db]


def transaccion_catalogo(fn: Callable[[object], T]) -> T:
    """Ejecuta fn(sesion) dentro de una transacción MongoDB (catálogo + outbox juntos).

    `with_transaction` reintenta automáticamente ante errores transitorios.
    """
    with mongo_cliente().start_session() as sesion:
        return sesion.with_transaction(fn)


# ---------------- Typesense ----------------
_ts: typesense.Client | None = None


def busqueda() -> typesense.Client:
    global _ts
    if _ts is None:
        _ts = typesense.Client({
            "nodes": [{"host": config.typesense_host, "port": config.typesense_port, "protocol": "http"}],
            "api_key": config.typesense_api_key,
            "connection_timeout_seconds": 2,
            "num_retries": 1,
            "retry_interval_seconds": 0.2,
        })
    return _ts


# ---------------- Redis ----------------
_redis: redis.Redis | None = None


def temporal() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis(
            host=config.redis_host,
            port=config.redis_port,
            password=config.redis_password,
            socket_timeout=2,
            socket_connect_timeout=2,
            decode_responses=True,
        )
    return _redis
