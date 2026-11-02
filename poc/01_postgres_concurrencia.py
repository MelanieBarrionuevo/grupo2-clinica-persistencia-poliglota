"""PoC Fase 2 · PostgreSQL para Agenda y Citas.

Demuestra:
  A. 20 pacientes reservan el MISMO cupo a la vez -> solo 1 obtiene la cita (RN-01, flujo 3).
  B. Un reintento con la misma clave de idempotencia devuelve la misma cita (RN-03).
  C. La base rechaza un cupo solapado del mismo profesional (RN-09).
  D. Cancelar libera el cupo sin borrar la cita, y se puede volver a reservar (RN-04, prueba P4).
"""
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg import errors

DSN = os.getenv(
    "PG_DSN",
    "postgresql://poc:poc@127.0.0.1:5432/poc?connect_timeout=5"
)

ESQUEMA = """
CREATE EXTENSION IF NOT EXISTS btree_gist;
DROP TABLE IF EXISTS evento_cita, cita, cupo;

CREATE TABLE cupo (
    id              SERIAL PRIMARY KEY,
    profesional_id  TEXT NOT NULL,
    sede_id         TEXT NOT NULL,
    servicio_id     TEXT NOT NULL,
    inicio          TIMESTAMPTZ NOT NULL,
    fin             TIMESTAMPTZ NOT NULL,
    estado          TEXT NOT NULL DEFAULT 'DISPONIBLE'
                    CHECK (estado IN ('DISPONIBLE', 'RESERVADO', 'BLOQUEADO')),
    bloqueado_hasta TIMESTAMPTZ,
    CHECK (fin > inicio),
    -- RN-09: un profesional no puede tener cupos solapados.
    CONSTRAINT cupo_sin_solape EXCLUDE USING gist (
        profesional_id WITH =, tstzrange(inicio, fin) WITH &&
    )
);

CREATE TABLE cita (
    id                 UUID PRIMARY KEY,
    cupo_id            INT NOT NULL REFERENCES cupo(id),
    paciente_id        TEXT NOT NULL,
    estado             TEXT NOT NULL CHECK (estado IN ('PENDIENTE', 'CONFIRMADA', 'CANCELADA')),
    clave_idempotencia TEXT NOT NULL UNIQUE,           -- RN-03
    creada_en          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- RN-01 (segunda barrera): una sola cita activa por cupo.
CREATE UNIQUE INDEX uq_cita_activa_por_cupo ON cita (cupo_id)
    WHERE estado IN ('PENDIENTE', 'CONFIRMADA');

CREATE TABLE evento_cita (
    id              BIGSERIAL PRIMARY KEY,
    cita_id         UUID NOT NULL REFERENCES cita(id),
    estado_anterior TEXT,
    estado_nuevo    TEXT NOT NULL,
    actor           TEXT NOT NULL,
    ocurrido_en     TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def reservar(cupo_id: int, paciente_id: str, clave: str) -> tuple[int, str]:
    """Devuelve (codigo_http, cita_id o mensaje). Una sola transacción local."""
    with psycopg.connect(DSN, autocommit=True) as conn:
        try:
            with conn.transaction():
                previa = conn.execute(
                    "SELECT id FROM cita WHERE clave_idempotencia = %s", (clave,)
                ).fetchone()
                if previa:
                    return 200, str(previa[0])  # reintento: misma cita

                # El UPDATE bloquea la fila; quien llegue segundo re-evalúa el WHERE y no la encuentra.
                fila = conn.execute(
                    """UPDATE cupo SET estado = 'RESERVADO'
                       WHERE id = %s AND estado = 'DISPONIBLE'
                       RETURNING id""",
                    (cupo_id,),
                ).fetchone()
                if fila is None:
                    # ¿Perdimos contra un reintento propio que ya se confirmó?
                    previa = conn.execute(
                        "SELECT id FROM cita WHERE clave_idempotencia = %s", (clave,)
                    ).fetchone()
                    if previa:
                        return 200, str(previa[0])
                    return 409, "franja ya no disponible"

                cita_id = uuid.uuid4()
                conn.execute(
                    """INSERT INTO cita (id, cupo_id, paciente_id, estado, clave_idempotencia)
                       VALUES (%s, %s, %s, 'PENDIENTE', %s)""",
                    (cita_id, cupo_id, paciente_id, clave),
                )
                conn.execute(
                    """INSERT INTO evento_cita (cita_id, estado_anterior, estado_nuevo, actor)
                       VALUES (%s, NULL, 'PENDIENTE', %s)""",
                    (cita_id, paciente_id),
                )
                return 201, str(cita_id)
        except errors.UniqueViolation:
            # Dos reintentos simultáneos con la misma clave: devolver la que ganó.
            fila = conn.execute(
                "SELECT id FROM cita WHERE clave_idempotencia = %s", (clave,)
            ).fetchone()
            return (200, str(fila[0])) if fila else (409, "conflicto")


def cancelar(cita_id: str, actor: str) -> None:
    with psycopg.connect(DSN) as conn, conn.transaction():
        cupo_id, estado = conn.execute(
            "SELECT cupo_id, estado FROM cita WHERE id = %s FOR UPDATE", (cita_id,)
        ).fetchone()
        conn.execute("UPDATE cita SET estado = 'CANCELADA' WHERE id = %s", (cita_id,))
        conn.execute("UPDATE cupo SET estado = 'DISPONIBLE' WHERE id = %s", (cupo_id,))
        conn.execute(
            """INSERT INTO evento_cita (cita_id, estado_anterior, estado_nuevo, actor)
               VALUES (%s, %s, 'CANCELADA', %s)""",
            (cita_id, estado, actor),
        )


def main() -> None:
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(ESQUEMA)
        inicio = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
        cupo_id = conn.execute(
            """INSERT INTO cupo (profesional_id, sede_id, servicio_id, inicio, fin)
               VALUES ('prof-001', 'sede-norte', 'serv-consulta', %s, %s) RETURNING id""",
            (inicio, inicio + timedelta(minutes=30)),
        ).fetchone()[0]

    # --- A. Concurrencia ---
    n = 20
    barrera = threading.Barrier(n)
    resultados: list[tuple[int, str]] = []
    lock = threading.Lock()

    def intento(i: int) -> None:
        barrera.wait()  # todos salen a la vez
        r = reservar(cupo_id, f"paciente-{i:02d}", f"clave-{i:02d}")
        with lock:
            resultados.append(r)

    hilos = [threading.Thread(target=intento, args=(i,)) for i in range(n)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    exitos = [r for r in resultados if r[0] == 201]
    rechazos = [r for r in resultados if r[0] == 409]
    print(f"A. {n} reservas simultáneas -> {len(exitos)} con 201, {len(rechazos)} con 409")
    assert len(exitos) == 1 and len(rechazos) == n - 1

    # --- B. Idempotencia ---
    ganadora = exitos[0][1]
    with psycopg.connect(DSN) as conn:
        clave = conn.execute(
            "SELECT clave_idempotencia FROM cita WHERE id = %s", (ganadora,)
        ).fetchone()[0]
    reintento = reservar(cupo_id, "cualquiera", clave)
    print(f"B. Reintento con la misma clave -> {reintento[0]}, misma cita: {reintento[1] == ganadora}")
    assert reintento == (200, ganadora)

    # --- C. Solapamiento ---
    try:
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(
                """INSERT INTO cupo (profesional_id, sede_id, servicio_id, inicio, fin)
                   VALUES ('prof-001', 'sede-sur', 'serv-consulta', %s, %s)""",
                (inicio + timedelta(minutes=15), inicio + timedelta(minutes=45)),
            )
        print("C. ERROR: se aceptó un cupo solapado")
        raise SystemExit(1)
    except errors.ExclusionViolation:
        print("C. Cupo solapado del mismo profesional -> rechazado por la base (ExclusionViolation)")

    # --- D. Cancelar y volver a reservar ---
    cancelar(ganadora, "paciente")
    nueva = reservar(cupo_id, "paciente-99", "clave-99")
    with psycopg.connect(DSN) as conn:
        eventos = conn.execute(
            "SELECT estado_nuevo FROM evento_cita WHERE cita_id = %s ORDER BY id", (ganadora,)
        ).fetchall()
    print(f"D. Tras cancelar: nueva reserva -> {nueva[0]}; historial de la cita cancelada: {[e[0] for e in eventos]}")
    assert nueva[0] == 201

    print("\nOK · PostgreSQL cumple RN-01, RN-03, RN-04 y RN-09")


if __name__ == "__main__":
    main()
