"""PoC Fase 2 · Redis para Sesiones y preferencias.

Demuestra:
  A. Guardar filtros y borrador de reserva con TTL.
  B. Recuperarlos antes del vencimiento.
  C. Que desaparecen solos al vencer (se usa un TTL corto para la demo; en la app será 1800 s).
"""
import os
import time

import redis

r = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    password=os.getenv("REDIS_PASSWORD") or None,
    decode_responses=True,
)

TTL_DEMO = int(os.getenv("TTL_DEMO_SEGUNDOS", "3"))
SESION = "sess-demo-123"


def main() -> None:
    r.ping()

    # --- A. Guardar con TTL ---
    k_filtros = f"filtros:{SESION}"
    k_borrador = f"borrador:{SESION}"
    with r.pipeline() as p:
        p.hset(k_filtros, mapping={"especialidad": "Cardiología", "sede": "Norte", "texto": "andrade"})
        p.expire(k_filtros, TTL_DEMO)
        p.hset(k_borrador, mapping={"profesional_id": "prof-001", "servicio_id": "serv-consulta", "cupo_id": "42"})
        p.expire(k_borrador, TTL_DEMO)
        p.execute()
    print(f"A. Guardados filtros y borrador con TTL={TTL_DEMO}s -> TTL restante: {r.ttl(k_borrador)}s")

    # --- B. Recuperar antes de vencer ---
    print("B. Antes de vencer:", r.hgetall(k_filtros), r.hgetall(k_borrador))
    assert r.hget(k_borrador, "cupo_id") == "42"

    # --- C. Vencimiento ---
    time.sleep(TTL_DEMO + 1)
    filtros, borrador = r.hgetall(k_filtros), r.hgetall(k_borrador)
    print(f"C. Después de {TTL_DEMO + 1}s -> filtros: {filtros or None}, borrador: {borrador or None}")
    assert not filtros and not borrador

    print("\nOK · Redis crea, recupera y caduca el estado temporal sin procesos de limpieza")


if __name__ == "__main__":
    main()
