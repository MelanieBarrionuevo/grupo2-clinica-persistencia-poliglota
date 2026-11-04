-- Clínica ambulatoria · Grupo 2
-- Fase 4: cuentas ficticias de demostración y titular de bloqueo temporal (hold).
-- Se ejecuta después de 01_schema.sql. Idempotente; no borra datos.

BEGIN;

-- Identidad de la demo (decisión de Fase 4): usuarios semilla por rol en PostgreSQL
-- y JWT firmado por la API. No hay registro abierto. Contraseñas con PBKDF2-SHA256.
CREATE TABLE IF NOT EXISTS usuario (
    id TEXT PRIMARY KEY CHECK (length(btrim(id)) > 0),
    usuario TEXT NOT NULL UNIQUE CHECK (length(btrim(usuario)) >= 3),
    nombre TEXT NOT NULL,
    rol TEXT NOT NULL CHECK (rol IN ('PACIENTE', 'RECEPCIONISTA', 'ADMINISTRADOR')),
    password_hash TEXT NOT NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Quién tiene el bloqueo temporal del cupo (RN-07). Solo ese usuario puede
-- convertirlo en reserva antes de que venza; vencido, cualquiera puede reservarlo.
ALTER TABLE cupo ADD COLUMN IF NOT EXISTS bloqueado_por TEXT;

COMMIT;
