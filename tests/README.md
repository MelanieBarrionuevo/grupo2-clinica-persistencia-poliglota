# Pruebas · Fases 4 y 5

Matriz completa, resultados y hallazgos: [docs/fase5-validacion.md](../docs/fase5-validacion.md).

## 1. Pruebas automatizadas

Con el sistema en marcha (`docker compose up --build -d`):

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r tests/requirements.txt

python tests/reporte.py              # suite + informe en docs/evidencias/fase5/ (no detiene contenedores)
python tests/reporte.py --fallos     # además P5: detiene y levanta contenedores
pytest -v -s tests/                  # lo mismo sin informe
```

| Archivo | Contenido |
| --- | --- |
| `test_flujos.py` | 14 pruebas de la Fase 4: P1–P4, idempotencia, reprogramación, roles, holds, desactivación, TTL |
| `test_validacion.py` | Fase 5, V-01 a V-19 (V-19: orden por cercanía): retraso medido, convergencia de la búsqueda, concurrencia e invariante de la agenda, la API no confía en el navegador, matriz de permisos, errores sin secretos |
| `test_fallos.py` | Fase 5, F-01 a F-07 (P5): reinicio completo y caída de cada gestor y del sincronizador. Solo con `--fallos` |
| `reporte.py` | Ejecuta la suite y escribe `docs/evidencias/fase5/resultados-AAAAMMDD-HHMM.md` |

`API_URL` permite apuntar a otra dirección (por defecto `http://localhost:8000/api/v1`). `CONTROL_CMD` cambia el comando con el que las pruebas de fallos detienen servicios (por defecto `docker compose {accion} {servicio}`). Las pruebas crean su propio catálogo con nombres únicos, así que se pueden repetir sin limpiar datos. Usan los pacientes `prueba1` a `prueba3` (no los de la demo) y al terminar cancelan sus citas y desactivan lo que crearon, así que no ensucian la demostración (`PACIENTES_PRUEBA` permite cambiar las cuentas).

### Pruebas de la Fase 4

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

## 2. P5 · Reinicios y caídas a mano (para mostrar en vivo en la defensa)

`test_fallos.py` automatiza estos escenarios. Los comandos sirven para repetirlos frente al docente mirando la web.

```bash
# a) Persistencia tras reinicio completo (sin -v: los volúmenes se conservan)
docker compose restart
#    → Mis citas sigue mostrando las mismas citas. Los filtros guardados en Redis se pierden (efímero).

# b) Búsqueda caída
docker compose stop typesense
#    → Inicio muestra "Buscador no disponible" y lista desde MongoDB; /api/v1/health = "degradado".
#    → Reservar sigue funcionando. Editar un profesional deja eventos pendientes en /tecnico.
docker compose start typesense
#    → En pocos segundos los eventos pendientes pasan a procesados (el worker reintenta cada ~5 s).

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
