# Fase 4 · Implementación — Clínica ambulatoria

**Grupo 2 · Proyecto A · Persistencia políglota**
**Producto de control:** repositorio ejecutable con `docker compose up --build -d`.

Este documento resume qué se construyó, cómo se cerraron las decisiones que la Fase 3 dejó pendientes y dónde está implementada cada regla de negocio. La especificación viva de la API está en `http://localhost:8000/docs` (OpenAPI) y exportada en [`openapi.json`](openapi.json).

## 1. Componentes y contenedores

| Servicio Compose | Imagen / código | Rol | Volumen |
| --- | --- | --- | --- |
| `postgres` | `postgres:16-alpine` + `db/postgres/*.sql` | Agenda, cupos, citas, historial y cuentas de demo (fuente oficial) | `pg_data` |
| `mongo` | `mongo:7.0` replica set `rs0` | Catálogo oficial + outbox | `mongo_data` |
| `mongo-init` | tarea única | Inicia `rs0` y aplica validadores/índices (`db/mongo/01_init.js`) | — |
| `typesense` | `typesense/typesense:28.0` | Proyección de búsqueda | `typesense_data` |
| `redis` | `redis:7.4-alpine` sin persistencia | Filtros y borradores con TTL | — (efímero a propósito) |
| `api` | `api/` FastAPI | Único punto de entrada; módulos de dominio | — |
| `seed` | misma imagen de la API | Datos ficticios, idempotente | — |
| `sync` | `sync/worker.py` + código de la API | Outbox MongoDB → Typesense | — |
| `web` | `web/` React + Vite servida por nginx | 6 pantallas; nginx hace proxy de `/api` | — |

Todos en la red interna `interna`. Solo se publican `127.0.0.1:3000` (web) y `127.0.0.1:8000` (API/OpenAPI); los gestores no exponen puertos.

```
api/app/
├── main.py            ensamblado de routers y manejo uniforme de errores
├── config.py          variables de entorno
├── db.py              clientes: pool PostgreSQL, MongoDB, Typesense, Redis
├── errores.py         {"error":{"codigo","mensaje"}} y 503 por gestor caído
├── seguridad.py       PBKDF2 + JWT; roles desde el token
├── proyeccion.py      construcción del documento de búsqueda y reindexación
├── seed.py            datos semilla
└── modulos/
    ├── catalogo.py    MongoDB + outbox (especialidades, sedes, servicios, profesionales)
    ├── busqueda.py    Typesense
    ├── agenda.py      cupos, holds y agenda de recepción (PostgreSQL)
    ├── citas.py       reservar, confirmar, cancelar, reprogramar (PostgreSQL)
    ├── sesiones.py    filtros y borrador (Redis)
    ├── auth.py        login de demo
    └── salud.py       /health, estado del sincronizador, reindexación
```

## 2. Decisiones que cierran los pendientes de la Fase 3

| Pendiente (Fase 3 §12 / contratos §9) | Decisión implementada |
| --- | --- |
| Identidad y roles | Usuarios semilla en la tabla `usuario` de PostgreSQL (`02_usuarios_y_holds.sql`), contraseña PBKDF2-SHA256, JWT HS256 firmado con `JWT_SECRET` (8 h). El rol y el `paciente_id` salen **siempre** del token. Sin registro abierto. |
| Reprogramación | Mueve la cita de cupo dentro de una transacción. Si reprograma **recepción**, se conserva el estado; si reprograma el **paciente**, vuelve a `PENDIENTE`. Evento `REPROGRAMADA` con `cupo_anterior_id` y `cupo_nuevo_id`. Solo a franjas del mismo servicio. |
| Holds | `POST /cupos/{id}/bloquear` deja el cupo `BLOQUEADO` para ese usuario durante `TTL_BLOQUEO_CUPO_SEGUNDOS` (300 s). Columna `bloqueado_por`. Un usuario mantiene un solo bloqueo. Un bloqueo vencido se trata como `DISPONIBLE` en consultas y reservas, y se limpia al listar (no depende de un proceso externo). |
| Outbox | Evento por cada mutación, en la misma transacción MongoDB, con `version` incremental. El worker reconstruye siempre desde el estado actual → aplicar un evento viejo o repetido es inocuo. Reintento con backoff exponencial (2, 4, 8… máx. 60 s). Reindexación completa: botón en pantalla técnica, `POST /sync/reindexar` o `docker compose exec sync python worker.py --reindexar`; además el worker la hace solo si detecta que la colección no existe. |
| Política de `/health` | `200 ok`; `200 degradado` si cae Typesense o Redis; `503 no_operativo` si cae PostgreSQL o MongoDB. Solo informa estado, latencia y tipo de error. |
| Búsqueda y paginación | `GET /buscar?q&especialidad_id&sede_id&modalidad&idioma&ordenar=relevancia|nombre&cerca=lat,lon&pagina&por_pagina≤50`, tolerancia a 2 errores de tipeo, facetas por especialidad, sede y modalidad. `cerca` (agregado en la Fase 5) ordena por la sede más cercana del profesional usando el campo `ubicaciones: geopoint[]` y devuelve `distancia_km`. |
| Concurrencia catálogo ↔ reserva | La API valida en MongoDB (profesional, servicio y sede activos) **antes** de la transacción PostgreSQL. No hay transacción distribuida: si el administrador desactiva justo en ese instante puede entrar una última reserva; queda registrada y recepción puede cancelarla. Ventana documentada, no oculta. |
| Cita creada por recepción | Nace `CONFIRMADA` (Fase 1, supuesto del equipo). La del paciente nace `PENDIENTE`. |

## 3. Trazabilidad de reglas

| Regla | Dónde se garantiza |
| --- | --- |
| RN-01 una cita activa por cupo | `UPDATE cupo … WHERE estado tomable RETURNING` (solo una transacción gana) + índice único parcial `uq_cita_activa_por_cupo` |
| RN-02 confirmar en el registro autoritativo | `citas.reservar` solo consulta PostgreSQL para decidir; Typesense y Redis no participan |
| RN-03 idempotencia | Cabecera `Idempotency-Key` (UUID) obligatoria; `UNIQUE (clave_idempotencia)`; reintento del mismo paciente y cupo → `200` con la misma cita; otra persona → `409` sin revelar datos |
| RN-04 cancelar sin perder historial | Cita pasa a `CANCELADA`, cupo a `DISPONIBLE`, evento `CANCELADA`; nada se borra |
| RN-05 reprogramar atómico | Bloqueo de cita y de ambos cupos en orden de id; si el destino no está libre, `409` y rollback completo |
| RN-06 trazabilidad | `evento_cita` con estados, cupos, `actor_id`, `actor_rol`, `ocurrido_en` |
| RN-07 hold con vencimiento | `agenda.bloquear_cupo`, condición `CUPO_TOMABLE` |
| RN-08 no reservar pasado ni inactivo | `inicio > now()` en el `UPDATE`; `catalogo.validar_reservable` |
| RN-09 sin solapes | `EXCLUDE USING gist` → `409 CUPO_SOLAPADO` (se publica todo el lote o nada) |
| RN-10 duración según servicio | `POST /cupos` calcula `fin` con `duracion_minutos` del catálogo; el navegador no envía `fin` |
| RN-11 borrado lógico | `PATCH … {"activo": false}`; el worker elimina el documento de la búsqueda |
| RN-12 búsqueda reconstruible | `proyeccion.reindexar_todo` |
| RN-13 degradación | Typesense caído → `503 BUSQUEDA_NO_DISPONIBLE` y la web cae a `GET /profesionales` (MongoDB); Redis caído → `503 ESTADO_TEMPORAL_NO_DISPONIBLE` solo en `/sesiones/*` |
| RN-14 privacidad | `/citas/mias` filtra por el usuario del token; `GET/PATCH /citas/{id}` devuelven `403` a otro paciente; las claves Redis se derivan del token |
| RN-15 sin secretos | `.env` fuera del repositorio; `/health` y `/sync/estado` sin credenciales |

## 4. Endpoints implementados (prefijo `/api/v1`)

| Método y ruta | Rol | Gestor |
| --- | --- | --- |
| `GET /health` | Público | Todos (solo estado) |
| `POST /auth/login`, `GET /auth/yo` | Público / autenticado | PostgreSQL |
| `GET/POST /especialidades`, `PATCH /especialidades/{id}` | Público / Admin | MongoDB + outbox |
| `GET/POST /sedes`, `PATCH /sedes/{id}` | Público / Admin | MongoDB + outbox |
| `GET/POST /servicios`, `PATCH /servicios/{id}` | Público / Admin | MongoDB + outbox |
| `GET /profesionales` (listado oficial / modo degradado), `GET /profesionales/{id}` | Público | MongoDB |
| `POST /profesionales`, `PATCH /profesionales/{id}` | Admin | MongoDB + outbox |
| `GET /buscar` | Público | Typesense |
| `GET /cupos` | Público | PostgreSQL |
| `POST /cupos` | Recepción, Admin | MongoDB (validar) + PostgreSQL |
| `POST /cupos/{id}/bloquear`, `POST /cupos/{id}/liberar` | Autenticado | PostgreSQL |
| `GET /agenda?fecha=` | Recepción, Admin | PostgreSQL (+ nombres de MongoDB) |
| `POST /citas` (`Idempotency-Key`) | Paciente, Recepción | MongoDB (validar) + PostgreSQL |
| `GET /citas/mias` | Paciente | PostgreSQL |
| `GET /citas`, `GET /pacientes` | Recepción, Admin | PostgreSQL |
| `GET /citas/{id}` (incluye historial) | Propietario, Recepción, Admin | PostgreSQL |
| `PATCH /citas/{id}/confirmar` | Recepción, Admin | PostgreSQL |
| `PATCH /citas/{id}/cancelar`, `/reprogramar` | Propietario, Recepción | PostgreSQL |
| `GET/PUT /sesiones/filtros`, `GET/PUT/DELETE /sesiones/borrador` | Autenticado | Redis |
| `GET /sync/estado`, `POST /sync/reindexar` | Recepción y Admin / Admin | MongoDB + Typesense |

Errores uniformes: `{"error": {"codigo": "CUPO_NO_DISPONIBLE", "mensaje": "…"}}`. Códigos principales: `VALIDACION` (422), `NO_AUTENTICADO`/`TOKEN_INVALIDO` (401), `SIN_PERMISO` (403), `CUPO_NO_DISPONIBLE`, `CUPO_BLOQUEADO`, `CUPO_PASADO`, `CUPO_SOLAPADO`, `TRANSICION_INVALIDA`, `CLAVE_IDEMPOTENCIA_REUTILIZADA`, `PROFESIONAL_INACTIVO` (409), `AGENDA_NO_DISPONIBLE`, `CATALOGO_NO_DISPONIBLE`, `BUSQUEDA_NO_DISPONIBLE`, `ESTADO_TEMPORAL_NO_DISPONIBLE` (503).

**¿Cuándo es seguro reintentar?** `POST /citas` siempre, con la misma `Idempotency-Key`. Los `PATCH` de citas son seguros: un segundo intento devuelve `409 TRANSICION_INVALIDA` sin efectos. Ante `503` no se confirmó ningún cambio.

## 5. Pantallas

| Pantalla | Ruta web | Qué demuestra |
| --- | --- | --- |
| Inicio / buscador | `/` | Typesense con tolerancia a tipeo, filtros, orden; filtros restaurados desde Redis; modo degradado con MongoDB |
| Detalle + reserva | `/profesional/:id` | Ficha (MongoDB), franjas (PostgreSQL), hold con cuenta regresiva, confirmación/error visible, reintento con la misma clave |
| Mis citas | `/mis-citas` | Estado, historial de eventos, cancelar y reprogramar |
| Recepción | `/recepcion` | Agenda del día, confirmar, cancelar, reprogramar, reservar para un paciente, publicar franjas |
| Administración | `/admin` | Alta, edición y desactivación de profesionales, servicios, especialidades y sedes |
| Técnico | `/tecnico` | Salud de los 4 gestores, outbox pendiente/procesado con retraso por evento, latido del worker, reindexación |

## 6. Verificación realizada

- La suite `tests/test_flujos.py` (14 pruebas: P1–P4, idempotencia, roles, privacidad, holds, reprogramación, desactivación, TTL) pasó contra la API con PostgreSQL 16 y Redis reales.
- Se comprobó: Typesense caído → búsqueda 503, `/health` degradado, reserva 201, eventos pendientes que se sincronizan al volver; índice borrado → el worker lo reconstruye; Redis caído → `/sesiones` 503 y la reserva sigue; PostgreSQL caído → reserva 503 y `/health` 503; al volver PostgreSQL, la API se recupera sola y las citas siguen ahí.
- Pendiente de ejecutar por el equipo en su equipo: `docker compose up --build -d` completo (la verificación anterior se hizo sin Docker Hub disponible, con MongoDB y Typesense simulados para la sincronización).

## 7. Límites conocidos y mejoras futuras

- MongoDB sin autenticación dentro de la red interna (no publica puerto). Para producción: usuario de aplicación + keyFile del replica set.
- El historial es append-only por contrato de la API; falta revocar `UPDATE/DELETE` sobre `evento_cita` con un rol de base de datos separado.
- Ventana catálogo ↔ reserva descrita en §2 (sin transacción distribuida, a propósito).
- Las citas en la búsqueda no se indexan (no es necesario); si se requiriera búsqueda de citas, sería otra proyección.
- Sin notificaciones al paciente ni recordatorios.
