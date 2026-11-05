# Fase 6 · Defensa — Clínica ambulatoria

**Grupo 2 · Daniel Guevara, Nicolás García, Camila Barrionuevo**
**Producto de control:** exposición y memoria técnica.

Guion para los 15–20 minutos del §11 del enunciado. Cada bloque dice qué decir, qué mostrar y qué comando usar. El reparto de bloques es una propuesta: ajústenlo, pero que cada integrante presente al menos uno y los tres puedan responder cualquier pregunta.

## 1. Preparación (antes de entrar)

Usar un equipo donde Typesense arranque (no el de Camila: ver Fase 5 §7).

```bash
docker compose down -v                 # empezar con datos limpios (solo la semilla)
docker compose up --build -d
docker compose ps -a                   # mongo-init y seed: Exited (0); el resto Up / healthy
curl http://localhost:8000/api/v1/health   # "estado": "ok"
```

- [ ] **No ejecutar las pruebas justo antes.** Si se ejecutan, se limpian solas, pero dejan franjas en días lejanos.
- [ ] Abrir pestañas: web como **paciente1** (`localhost:3000`), una **ventana privada** como **paciente2**, otra como **admin** en `/tecnico`, y `localhost:8000/docs`.
- [ ] Dejar una terminal en la raíz del proyecto con los comandos de la sección 4 listos para pegar.
- [ ] Opcional, para mostrar el vencimiento del hold en vivo: `TTL_BLOQUEO_CUPO_SEGUNDOS=20` en `.env` y `docker compose up -d api`.
- [ ] Tener abierto [`docs/fase2-investigacion.md`](fase2-investigacion.md) §4 (matriz) y el informe por si piden un detalle.

## 2. Guion (18 minutos)

| # | Bloque (§11) | Tiempo | Quién (propuesta) |
| --- | --- | --- | --- |
| 1 | Problema y dominios | 2 min | Camila |
| 2 | Matriz comparativa y gestor descartado | 4 min | Daniel |
| 3 | `docker compose up` y servicios | 2 min | Nicolás |
| 4 | Dos flujos completos en la web, comprobando los gestores | 6 min | Camila y Nicolás |
| 5 | Fallo o concurrencia: consistencia y recuperación | 3 min | Daniel |
| 6 | Cambio imprevisto de requisito y límites | 1–3 min | Los tres |

### Bloque 1 · Problema y dominios (2 min)

**Decir:** una red de consultorios necesita que los pacientes busquen, reserven y sigan sus citas sin que nunca se confirme una doble reserva. Hay cinco dominios con necesidades distintas: el catálogo tiene fichas variables, la agenda exige consistencia fuerte, la búsqueda tolera segundos de retraso y las sesiones son temporales. Por eso no basta un solo gestor.

**Mostrar:** la Figura 1 del informe (mapa de dominios): Agenda y Citas comparten una transacción y la búsqueda es una proyección del catálogo.

### Bloque 2 · Matriz y gestor descartado (4 min)

**Decir**, por dominio, en una frase: patrón de acceso → gestor → por qué.

- **Agenda y citas → PostgreSQL.** Tres reglas críticas se garantizan con restricciones de la base, no con código: `UPDATE … WHERE estado='DISPONIBLE'` (un solo ganador), índice único parcial (una cita activa por cupo) y `EXCLUDE USING gist` (sin solapes).
- **Descartado: MongoDB para la agenda.** Reprogramar toca dos cupos, una cita y un evento: necesita transacciones multi-documento, y la regla de no solapamiento quedaría en la aplicación.
- **Catálogo → MongoDB:** fichas con atributos variables, validadas con `$jsonSchema`, y el outbox en la misma transacción.
- **Búsqueda → Typesense:** tolerancia a errores de tipeo, facetas y orden por cercanía. **Descartado: Elasticsearch**, que pide 1–2 GB de memoria para unos 300 documentos.
- **Sesiones → Redis:** TTL nativo; perderlo no afecta al negocio.

**Pregunta de investigación (§8), una línea:** consistencia fuerte para todo lo que decide la ocupación (reservar, cancelar, reprogramar, confirmar, hold); eventual para la búsqueda, porque al reservar siempre se vuelve a validar en la fuente oficial.

### Bloque 3 · Docker Compose (2 min)

```bash
docker compose up --build -d     # ya levantado: se ejecuta igual y responde "Running"
docker compose ps
```

**Señalar:** solo `api` (8000) y `web` (3000) publican puertos, y solo en `127.0.0.1`. Los gestores aparecen con `5432/tcp`, `27017/tcp`… sin `->`, así que no son accesibles desde fuera (RS-02). `mongo-init` y `seed` son tareas únicas: terminan en `Exited (0)`. Hay volúmenes para PostgreSQL, MongoDB y Typesense; Redis no tiene a propósito.

### Bloque 4 · Dos flujos completos (6 min)

**Flujo A · alta en el catálogo → búsqueda (flujos 1 y 5 del enunciado)**

1. Como **admin**, en Administración, crear un profesional (p. ej. "Dra. Defensa", Cardiología, Sede Norte).
2. Ir a **Técnico**: aparece el evento `profesional.creado` procesado con su retraso en milisegundos (outbox de MongoDB → sincronizador → Typesense).
3. En **Inicio**, buscar `defensa` o con un error de tipeo (`cardiolgia`): aparece, con la etiqueta *Typesense · proyección*.
4. Elegir **«Más cercanas»**: las tarjetas muestran «a X km» desde la sede más cercana.
5. Comprobar en el gestor:

```bash
docker compose exec mongo mongosh catalogo --quiet --eval 'db.outbox.find().sort({creado_en:-1}).limit(1).toArray()'
```

**Flujo B · reserva sin doble cupo, cancelación y nueva reserva (flujos 2 y 3)**

1. Como **paciente1**, abrir la ficha: los datos vienen de *MongoDB · fuente oficial* y las franjas de *PostgreSQL · autoritativo*.
2. Elegir una franja: queda bloqueada 5 minutos (hold en PostgreSQL, no en Redis).
3. En la ventana privada, **paciente2** pulsa «Actualizar»: la misma franja aparece tachada y deshabilitada, porque PostgreSQL la tiene bloqueada para paciente1. Si alguien la intentara tomar por la API, recibiría `409 CUPO_BLOQUEADO`.
4. paciente1 confirma: «¡Cita registrada!», estado *Pendiente*, ID de la cita, franja tachada.
5. Comprobar en PostgreSQL:

```bash
docker compose exec postgres psql -U clinica_app -d clinica -c "SELECT c.id, c.estado, c.cupo_id, cu.estado AS cupo FROM cita c JOIN cupo cu ON cu.id = c.cupo_id ORDER BY c.creada_en DESC LIMIT 3;"
```

6. En **Mis citas**, cancelar: la cita pasa a *Cancelada* con su historial CREADA → CANCELADA, y paciente2 ya puede reservar la misma franja (P4).

```bash
docker compose exec postgres psql -U clinica_app -d clinica -c "SELECT tipo, estado_anterior, estado_nuevo, actor_rol, ocurrido_en FROM evento_cita ORDER BY id DESC LIMIT 4;"
```

### Bloque 5 · Fallo y recuperación (3 min)

Elegir **uno**, según el tiempo. Lo más claro es la búsqueda caída (P5):

```bash
docker compose stop typesense
```

1. **Técnico:** Typesense en rojo, estado general *degradado* y aviso con la causa.
2. **Inicio:** aviso «Buscador no disponible»; el listado sale de MongoDB.
3. **Reservar sigue funcionando:** la decisión ocurre en PostgreSQL (RN-02, RN-13).
4. Editar la sede de un profesional en Administración: el evento queda **pendiente** en el outbox, no se pierde.

```bash
docker compose start typesense
```

5. En pocos segundos el evento pasa a procesado y la búsqueda refleja el cambio. **Decir:** entrega al menos una vez, con aplicación idempotente, porque el sincronizador siempre reconstruye desde el estado actual de MongoDB.

**Alternativa de concurrencia (30 s):** `pytest -v tests/test_flujos.py -k p3`. Diez reservas simultáneas del mismo cupo dan exactamente un `201` y nueve `409`.

**Cerrar con números:** 44/44 pruebas con Docker real; retraso alta → búsqueda de 1 s; recuperación de 3 a 10 s según el gestor (informe, tabla 12).

### Bloque 6 · Cambio de requisito y límites (1–3 min)

Responder con la misma estructura: **qué dominio cambia → qué gestor → qué consistencia → qué se toca → riesgo**. Ver la sección 3.

Límites para mencionar si sobra tiempo: no hay transacción distribuida catálogo ↔ reserva (la ventana está documentada), cada gestor tiene un solo nodo y MongoDB no tiene autenticación dentro de la red interna (informe, sección 12).

## 3. Cambios de requisito ensayados

| Cambio que podría pedir el docente | Respuesta (qué se toca) | Gestor y consistencia |
| --- | --- | --- |
| **Lista de espera** cuando no hay franjas | Tabla `lista_espera (paciente, profesional, servicio, creada_en)`. Al cancelar, en la **misma transacción** que libera el cupo, se toma el primero con `SELECT … FOR UPDATE SKIP LOCKED` y se le crea la cita (o un hold de 30 min para que confirme) | PostgreSQL · fuerte: es ocupación de cupos |
| **Quitar un gestor** (usar solo tres) | Se retira MongoDB: el catálogo pasa a PostgreSQL con `JSONB` para los atributos variables, y el outbox pasa a ser una tabla en la misma transacción. Typesense y Redis no cambian (Fase 2 §6) | PostgreSQL · fuerte local; búsqueda sigue eventual |
| **Recordatorios por correo** 24 h antes | Outbox de citas en PostgreSQL (evento en la misma transacción que crea o cancela la cita) y un worker que envía. Nunca se envía dentro de la transacción; la clave del evento evita duplicados | PostgreSQL · eventual para la notificación |
| **Plazo mínimo para cancelar** (24 h) | Regla en `PATCH /citas/{id}/cancelar` comparando con `now()` dentro de la transacción; `409 PLAZO_VENCIDO`. Recepción puede seguir cancelando | PostgreSQL · fuerte |
| **Máximo una cita activa por paciente y especialidad** | Guardar `especialidad_id` en la cita y añadir un índice único parcial `(paciente_id, especialidad_id) WHERE estado IN ('PENDIENTE','CONFIRMADA')` | PostgreSQL · fuerte, declarativa |
| **Buscar por horario libre** ("mañana a las 9") | No poner la disponibilidad en Typesense como fuente. Opción: proyectar la "próxima franja libre" como dato orientativo (eventual) y confirmar siempre en PostgreSQL al reservar | Typesense eventual + PostgreSQL fuerte |
| **Alta disponibilidad** | Replica set de 3 nodos en MongoDB, réplica de PostgreSQL con failover, clúster Raft de Typesense. El código no cambia: ya tolera caídas y reintentos | Infraestructura |
| **Pagos** | Fuera del alcance del enunciado. Si se agregara: pago en estado propio, idempotente, y la cita se confirma solo con el pago confirmado (saga, sin transacción distribuida) | PostgreSQL · fuerte |

## 4. Comandos de inspección

```bash
# PostgreSQL: últimas citas e historial
docker compose exec postgres psql -U clinica_app -d clinica -c "SELECT id, estado, cupo_id FROM cita ORDER BY creada_en DESC LIMIT 5;"
docker compose exec postgres psql -U clinica_app -d clinica -c "SELECT tipo, estado_anterior, estado_nuevo, actor_rol FROM evento_cita ORDER BY id DESC LIMIT 5;"

# MongoDB: ficha y outbox
docker compose exec mongo mongosh catalogo --quiet --eval 'db.profesionales.findOne({_id:"prof-001"})'
docker compose exec mongo mongosh catalogo --quiet --eval 'db.outbox.find().sort({creado_en:-1}).limit(3).toArray()'

# Redis: claves temporales y su TTL
docker compose exec redis sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning --scan --pattern "sesion:*"'
docker compose exec redis sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning TTL sesion:pac-001:filtros'

# Typesense: documentos indexados
docker compose exec api python -c "from app.db import busqueda; print(busqueda().collections['profesionales'].retrieve()['num_documents'])"
```

## 5. Preguntas probables

| Pregunta | Respuesta corta |
| --- | --- |
| ¿Por qué no una transacción entre MongoDB y PostgreSQL? | No existe ACID entre gestores. Se valida el catálogo antes y se reserva en una transacción local; la ventana mínima está documentada y se resuelve cancelando |
| ¿Qué pasa si el sincronizador falla a mitad? | El evento no se marca procesado hasta que Typesense responde. Se reintenta, y como se reconstruye desde el estado actual, repetirlo es inocuo |
| ¿Y si se pierde el índice de búsqueda? | El worker detecta la colección ausente (o un esquema viejo) y reindexa todo desde MongoDB; también hay un botón en Técnico |
| ¿Cómo evitan la doble reserva con reintentos? | `Idempotency-Key` con `UNIQUE`: el mismo paciente con la misma clave recibe la misma cita (200); otra persona con esa clave recibe 409 sin ver datos ajenos |
| ¿Por qué el hold está en PostgreSQL y no en Redis? | Porque es parte de la disponibilidad: si viviera en Redis, la decisión dependería de una caché (lo prohíbe la regla 6) |
| ¿Cuál es la fuente oficial de cada dato? | Catálogo: MongoDB. Cupos, citas e historial: PostgreSQL. Búsqueda: copia derivada. Filtros: Redis, efímero |
| ¿Por qué cuatro gestores? | Cada uno cubre algo que los otros hacen mal; si hubiera que quitar uno, sería MongoDB (ver cambio de requisito) |
| ¿La web decide permisos? | No. Ocultar botones es comodidad; la API valida el rol del JWT en cada operación (pruebas V-10 y V-11) |
| ¿Cómo prueban que funciona? | 45 pruebas automatizadas, incluidas caídas reales de cada gestor; informe con fecha en `docs/evidencias/fase5/` |

## 6. Lista final de entregables (§10)

| # | Entregable | Dónde |
| --- | --- | --- |
| 1 | README con ejecución, credenciales, URLs, reinicio y limpieza | `README.md` |
| 2 | Docker Compose, Dockerfiles, volúmenes y variables | `docker-compose.yml`, `*/Dockerfile`, `.env.example` |
| 3 | Informe técnico | Se entrega por separado; el detalle está en docs/fase1 a fase5 |
| 4 | Arquitectura, dos flujos end-to-end y tabla de consistencia | Informe, secciones 3, 5 y 6; `docs/diagramas/` |
| 5 | Especificación de API | `docs/openapi.json` y `/docs` |
| 6 | Web operativa | `web/` |
| 7 | Datos semilla y pruebas | `api/app/seed.py`, `tests/` |
| 8 | Evidencias y reflexión sobre límites | `docs/evidencias/fase5/` (informes y capturas); informe, sección 12 |
