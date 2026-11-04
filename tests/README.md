# Pruebas · Fase 4/5

## 1. Pruebas automatizadas (P1–P4 y reglas de negocio)

Con el sistema en marcha (`docker compose up --build -d`):

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r tests/requirements.txt
pytest -v -s tests/
```

`API_URL` permite apuntar a otra dirección (por defecto `http://localhost:8000/api/v1`). Las pruebas crean su propio catálogo con nombres únicos, así que se pueden repetir sin limpiar datos.

| Prueba | Qué verifica |
| --- | --- |
| `test_p1_…` | Profesional nuevo aparece en `/buscar` (imprime el retraso medido) |
| `test_p2_…` | Cambio de sede en MongoDB se refleja en Typesense; evento outbox v2 con `retraso_ms` |
| `test_p3_…` | 10 reservas simultáneas al mismo cupo → exactamente un `201` y nueve `409` |
| `test_p4_…` | Cancelar libera el cupo, otro paciente lo reserva, el historial se conserva |
| `test_reintento_…` | Misma `Idempotency-Key` → misma cita; usada por otro → `409` |
| `test_reprogramacion_atomica` | A franja ocupada `409` sin cambios; a franja libre, movimiento atómico |
| `test_bloqueo_temporal_hold` | Hold de un paciente impide la reserva de otro |
| `test_profesional_desactivado_…` | Inactivo → `409` al reservar y desaparece de la búsqueda |
| `test_roles_…`, `test_paciente_solo_ve_…` | Autorización en la API, no en la web |
| `test_filtros_y_borrador_con_ttl` | Redis con TTL y aislamiento por usuario |

## 2. P5 · Reinicios y caídas (manual, para la defensa)

```bash
# a) Persistencia tras reinicio completo (sin -v: los volúmenes se conservan)
docker compose restart
#    → Mis citas sigue mostrando las mismas citas. Los filtros guardados en Redis se pierden (efímero).

# b) Búsqueda caída
docker compose stop typesense
#    → Inicio muestra "Buscador no disponible" y lista desde MongoDB; /api/v1/health = "degradado".
#    → Reservar sigue funcionando. Editar un profesional deja eventos pendientes en /tecnico.
docker compose start typesense
#    → En segundos los eventos pendientes pasan a procesados (backoff máx. 60 s).

# c) Pérdida total del índice
docker compose rm -sf typesense && docker volume rm clinica_typesense_data && docker compose up -d typesense
#    → El worker detecta la colección ausente y reindexa todo desde MongoDB (ver "última reindexación").

# d) Estado temporal caído
docker compose stop redis      # /sesiones → 503; reservar sigue funcionando
docker compose start redis     # filtros/borradores anteriores ya no existen (por diseño)

# e) Agenda caída
docker compose stop postgres   # reservar → 503 AGENDA_NO_DISPONIBLE; /health → 503 no_operativo
docker compose start postgres  # la API se reconecta sola; las citas siguen ahí

# f) Sincronizador detenido
docker compose stop sync       # /tecnico: worker "detenido", eventos se acumulan; nada se pierde
docker compose start sync
```

## 3. Inspeccionar los gestores durante la demo

```bash
docker compose exec postgres psql -U clinica_app -d clinica \
  -c "SELECT id, estado, cupo_id FROM cita ORDER BY creada_en DESC LIMIT 5;" \
  -c "SELECT tipo, estado_anterior, estado_nuevo, actor_rol, ocurrido_en FROM evento_cita ORDER BY id DESC LIMIT 5;"

docker compose exec mongo mongosh catalogo --quiet \
  --eval 'db.outbox.find().sort({creado_en:-1}).limit(3).toArray()'

docker compose exec redis sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning --scan --pattern "sesion:*"'

docker compose exec api python -c "from app.db import busqueda; print(busqueda().collections['profesionales'].retrieve()['num_documents'])"
```
