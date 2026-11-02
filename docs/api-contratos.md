# Fase 3 · Contratos de API REST — Clínica ambulatoria

**Grupo 2 · Proyecto A · Persistencia políglota**  
**Estado:** especificación propuesta para implementación en FastAPI. Ninguno de estos endpoints existe todavía en la aplicación final.

## 1. Convenciones generales

- Prefijo: `/api/v1` para los endpoints del negocio; documentación automática de FastAPI en `/docs`.
- Solicitudes y respuestas: `application/json`; fechas en ISO 8601 con zona horaria, por ejemplo `2026-11-03T14:00:00-05:00`.
- Identificadores del catálogo: cadenas estables (`prof-001`, `sede-norte`, `esp-cardio`, `serv-consulta`); cupos: entero; citas: UUID.
- El navegador nunca se conecta a los gestores ni envía claves de bases de datos.
- Para operaciones autenticadas se utilizará `Authorization: Bearer <token>`; **la estrategia de identidad sigue siendo una decisión de diseño pendiente**. Los roles del enunciado son `PACIENTE`, `RECEPCIONISTA` y `ADMINISTRADOR`.
- No confiar en `paciente_id`, roles, precio, disponibilidad ni estados proporcionados por el navegador: derivar el actor de una identidad verificada y consultar la fuente oficial.
- Errores uniformes propuestos: `{"error":{"codigo":"CUPO_OCUPADO","mensaje":"La franja ya no está disponible"}}`.
- Códigos: `200` lectura/cambio; `201` creación; `400` regla o solicitud inválida; `401` sin autenticación; `403` sin permiso; `404` inexistente; `409` conflicto de negocio; `422` cuerpo o parámetros inválidos; `503` dependencia crítica indisponible. Con FastAPI, personalizar manejadores si se exige el formato uniforme para todos los errores.
- Este documento es contrato de diseño: antes de comenzar la API deben cerrarse las decisiones marcadas **PENDIENTE**.

## 2. Mapa de endpoints

| Método | Ruta (bajo `/api/v1`) | Rol | Fuente de lectura/escritura | Éxito | Errores destacados |
| --- | --- | --- | --- | --- | --- |
| GET | `/health` | Público | Estado de API y dependencias | 200 / 503 según política | — |
| GET | `/especialidades` | Público | MongoDB | 200 | 503 |
| POST | `/especialidades` | Administrador | MongoDB | 201 | 409, 422 |
| PATCH | `/especialidades/{id}` | Administrador | MongoDB | 200 | 404, 409, 422 |
| GET | `/sedes` | Público | MongoDB | 200 | 503 |
| POST | `/sedes` | Administrador | MongoDB | 201 | 409, 422 |
| PATCH | `/sedes/{id}` | Administrador | MongoDB | 200 | 404, 422 |
| GET | `/profesionales/{id}` | Público | MongoDB | 200 | 404, 503 |
| POST | `/profesionales` | Administrador | MongoDB + outbox | 201 | 409, 422 |
| PATCH | `/profesionales/{id}` | Administrador | MongoDB + outbox | 200 | 404, 422 |
| GET | `/servicios` | Público | MongoDB | 200 | 503 |
| POST | `/servicios` | Administrador | MongoDB + outbox | 201 | 409, 422 |
| PATCH | `/servicios/{id}` | Administrador | MongoDB + outbox | 200 | 404, 422 |
| GET | `/buscar` | Público | Typesense | 200 | 503 |
| GET | `/cupos` | Público | PostgreSQL | 200 | 422, 503 |
| POST | `/cupos` | Recepción, Administrador | MongoDB validación + PostgreSQL escritura | 201 | 409, 422, 503 |
| POST | `/citas` | Paciente, Recepción | MongoDB validación + PostgreSQL transacción | 201; 200 reintento | 403, 409, 422, 503 |
| GET | `/citas/mias` | Paciente | PostgreSQL, identidad verificada | 200 | 401, 503 |
| GET | `/citas/{id}` | Propietario, Recepción, Administrador (lectura) | PostgreSQL | 200 | 403, 404 |
| PATCH | `/citas/{id}/cancelar` | Propietario, Recepción | PostgreSQL transacción | 200 | 403, 404, 409 |
| PATCH | `/citas/{id}/confirmar` | Recepción | PostgreSQL transacción | 200 | 403, 404, 409 |
| PATCH | `/citas/{id}/reprogramar` | Propietario, Recepción | PostgreSQL transacción | 200 | 403, 404, 409 |
| PUT | `/sesiones/filtros` | Usuario de sesión | Redis | 200 | 503 o degradación documentada |
| GET | `/sesiones/filtros` | Usuario de sesión | Redis | 200 | 503 o degradación documentada |
| PUT | `/sesiones/borrador` | Usuario de sesión | Redis | 200 | 422, 503 |
| GET | `/sesiones/borrador` | Usuario de sesión | Redis | 200 | 404 / vacío si caduca |

Para un CRUD completo se añadirán endpoints de consulta de listado/detalle y desactivación cuando corresponda, sin borrado físico de registros históricos. Las rutas de autenticación se especificarán al cerrar la fuente de identidades.

## 3. Contrato de catálogo (MongoDB)

### `POST /api/v1/profesionales` · Administrador

```json
{
  "nombre": "Dra. Lucía Andrade",
  "especialidad_ids": ["esp-cardio"],
  "sedes": [{"sede_id": "sede-norte", "horario_ref": "L-V 08:00-13:00"}],
  "idiomas": ["es", "en"],
  "modalidades": ["PRESENCIAL"]
}
```

**201**:

```json
{"id":"prof-001","nombre":"Dra. Lucía Andrade","activo":true,"version":1,"sincronizacion":"PENDIENTE"}
```

Validar nombre, especialidades y sedes existentes/activas, modalidades permitidas y forma de los arreglos. `profesional` y su evento de `outbox` se escriben en la **misma transacción MongoDB** (requiere replica set). No responder que la búsqueda ya está actualizada: la proyección es eventual.

### `PATCH /api/v1/profesionales/{id}` · Administrador

Ejemplo de cambio de sede:

```json
{"sedes":[{"sede_id":"sede-sur","horario_ref":"L-V 09:00-13:00"}]}
```

**200**: `{"id":"prof-001","version":2,"sincronizacion":"PENDIENTE"}`. Los cambios de sede, especialidad o actividad generan evento outbox. El borrado lógico se hace con `{"activo":false}`, sin eliminar citas históricas.

### `POST /api/v1/servicios` · Administrador

```json
{"nombre":"Consulta cardiológica","especialidad_id":"esp-cardio","duracion_minutos":30,"modalidades":["PRESENCIAL"],"activo":true}
```

**201**: `{"id":"serv-consulta","activo":true}`. Duración positiva. Diseñar la validación de compatibilidad de especialidad, modalidad y duración cuando se publique un cupo.

## 4. Contrato de búsqueda (Typesense)

### `GET /api/v1/buscar?q=cardiolgia&especialidad_id=esp-cardio&sede_id=sede-sur`

**200** (ejemplo):

```json
{
  "total": 1,
  "resultados": [
    {"profesional_id":"prof-001","nombre":"Dra. Lucía Andrade","especialidades":["Cardiología"],"sedes":["Sur"]}
  ],
  "origen": "typesense"
}
```

La búsqueda tolera consistencia eventual. **No incluir cupos como confirmados** en esta respuesta: la disponibilidad se consulta en PostgreSQL. Si Typesense cae, informar `503` con `BUSQUEDA_NO_DISPONIBLE`; las operaciones de citas existentes deben seguir siendo accesibles por sus rutas directas.

## 5. Contrato de agenda (PostgreSQL)

### `POST /api/v1/cupos` · Recepción / Administrador

```json
{
  "profesional_id":"prof-001",
  "sede_id":"sede-norte",
  "servicio_id":"serv-consulta",
  "inicio":"2026-11-03T09:00:00-05:00",
  "fin":"2026-11-03T09:30:00-05:00"
}
```

**201**: `{"cupo_id":42,"estado":"DISPONIBLE"}`. Validar profesional, sede y servicio activos; inicio futuro; `fin > inicio`; duración y modalidad coherentes; impedir solapes con restricción PostgreSQL `EXCLUDE USING gist`. En solapamiento devolver `409`.

### `GET /api/v1/cupos?profesional_id=prof-001&sede_id=sede-norte&desde=...&hasta=...`

**200**:

```json
{"cupos":[{"cupo_id":42,"inicio":"2026-11-03T09:00:00-05:00","fin":"2026-11-03T09:30:00-05:00","estado":"DISPONIBLE"}]}
```

Los horarios ofrecidos se leen desde PostgreSQL, no de Typesense ni Redis. No se garantizan libres hasta terminar la transacción de reserva.

## 6. Contrato crítico de citas (PostgreSQL)

### `POST /api/v1/citas` · Paciente / Recepción

Encabezado obligatorio: `Idempotency-Key: <uuid>`.

```json
{"cupo_id":42,"servicio_id":"serv-consulta"}
```

**201** (primera reserva):

```json
{
  "cita_id":"a0f2099d-7ac5-4db4-828c-12f78f59e0a1",
  "cupo_id":42,
  "estado":"PENDIENTE"
}
```

**200**: una repetición idéntica del mismo paciente con la misma clave devuelve la **misma cita**. Si la clave corresponde a otra operación/paciente, devolver `409` sin revelar información privada. **409** si la franja está ocupada. **422** si el cuerpo o clave no son válidos. **503** si el catálogo o la base transaccional no permiten decidir con seguridad.

Pasos del servidor: (1) identidad verificada; (2) consultar MongoDB para profesional/servicio activos; (3) transacción PostgreSQL: verificar clave de idempotencia vinculada al actor y petición, `UPDATE cupo SET estado='RESERVADO' WHERE id=:id AND estado='DISPONIBLE' RETURNING id`, insertar cita e insertar evento; (4) confirmar transacción; (5) responder. Índice parcial de cita activa por cupo como segunda barrera. **No hay transacción distribuida** con MongoDB.

### `GET /api/v1/citas/mias` · Paciente

**200**:

```json
{"citas":[{"cita_id":"a0f2099d-7ac5-4db4-828c-12f78f59e0a1","cupo_id":42,"estado":"PENDIENTE"}]}
```

La identidad del paciente se deriva del token verificado. Un paciente nunca puede listar citas ajenas.

### `PATCH /api/v1/citas/{id}/confirmar` · Recepción

**200**: `{"cita_id":"...","estado":"CONFIRMADA"}`. Solo `PENDIENTE → CONFIRMADA`; insertar evento en la misma transacción. Si la cita fue cancelada u otra transición es inválida: `409`.

### `PATCH /api/v1/citas/{id}/cancelar` · Paciente propietario / Recepción

**200**: `{"cita_id":"...","estado":"CANCELADA","cupo_liberado":true}`. Bloquear cita, comprobar autorización, cambiar estado, liberar cupo y agregar evento en la **misma transacción PostgreSQL**. No borrar historial; una cancelada no se reactiva.

### `PATCH /api/v1/citas/{id}/reprogramar` · Paciente propietario / Recepción

```json
{"nuevo_cupo_id":43}
```

**200**:

```json
{"cita_id":"...","estado":"PENDIENTE","cupo_anterior_id":42,"cupo_nuevo_id":43}
```

El estado de respuesta es **provisional**: conservar `PENDIENTE` o `CONFIRMADA` según estado previo es una decisión pendiente. La operación debe ocupar el nuevo cupo y liberar el antiguo en una sola transacción, registrar explícitamente ambos identificadores en el evento y revertir todo ante conflicto (`409`). Bloquear las filas en orden fijo para reducir interbloqueos. Validar compatibilidad del profesional, servicio y franja.

## 7. Contrato de sesión (Redis)

`PUT /api/v1/sesiones/filtros`:

```json
{"especialidad_id":"esp-cardio","sede_id":"sede-norte","texto":"andrade"}
```

**200**: `{"guardado":true,"ttl_segundos":1800}`. `GET /api/v1/sesiones/filtros` devuelve los filtros si no expiraron. El identificador de sesión debe quedar asociado al usuario/sesión del servidor: no permitir leer arbitrariamente otra sesión indicando un ID ajeno.

`PUT /api/v1/sesiones/borrador`:

```json
{"profesional_id":"prof-001","servicio_id":"serv-consulta","cupo_id":42}
```

**200**: `{"guardado":true,"ttl_segundos":1800}`. El borrador **no ocupa ni bloquea** el cupo. Si Redis no está disponible, el usuario puede seguir reservando a través de PostgreSQL, aunque pierde comodidad de sesión.

## 8. Consistencia, errores y pruebas de aceptación

| Situación | Respuesta/recuperación | Prueba |
| --- | --- | --- |
| Dos solicitudes simultáneas sobre el mismo cupo | Una `201`, la otra `409` | Lanzar dos peticiones concurrentes con claves distintas; comprobar una sola cita activa |
| Reintento idéntico | `200`, mismo ID, no duplica | Repetir encabezado y cuerpo |
| Cancelación | Cita `CANCELADA`, cupo `DISPONIBLE`, evento persistente | Consultar PostgreSQL y reservar de nuevo |
| Reprogramación a franja ocupada | `409`, sin cambios parciales | Comparar cita y estados de ambos cupos |
| Modificación de sede/especialidad | MongoDB confirma, outbox pendiente, Typesense se actualiza después | Medir retraso y mostrar estado del worker |
| Typesense detenido | Búsqueda degradada; citas y agenda siguen operables por rutas directas | Detener solo Typesense y repetir operaciones |
| Redis detenido | No se conservan filtros/borradores; citas siguen operables | Detener solo Redis |
| PostgreSQL detenido | No confirmar reservas | Verificar `503`, sin crear citas inexistentes |
| Reinicio de contenedores | Volúmenes recuperan catálogo, citas e índices persistidos | Reiniciar sin borrar volúmenes |

## 9. Decisiones a cerrar antes de implementar (no inventar como hechos consumados)

1. **Identidad y roles:** dónde se almacenan cuentas ficticias, cómo se emiten/verifican tokens y qué rol puede crear usuarios. Debe existir validación real del lado API; no basta enviar un rol desde React.
2. **Reprogramación:** preservar estado previo o volver a `PENDIENTE`; agregar los campos de cupo anterior/nuevo al evento de historial.
3. **Holds:** confirmar si habrá bloqueo temporal por cinco minutos y cómo se liberan bloqueos vencidos desde PostgreSQL; si se implementa, definir endpoints y pruebas.
4. **Outbox:** orden de versiones, resolución de eventos antiguos, reintento, backoff y reconstrucción completa de Typesense.
5. **Contrato de salud:** si `/health` informa degradación parcial con `200` o `503`; nunca revelar contraseñas ni secretos.
6. **Parámetros de búsqueda y paginación:** valores máximos, orden y filtros definitivos.

## 10. Referencias internas

- `docs/fase1-comprension.md`: roles, historias de usuario, máquinas de estados y RN-01 a RN-15.
- `docs/fase2-investigacion.md`: selección de gestores, matriz comparativa y decisiones D-01 a D-10.
- `docs/fase3-diseno.md`: arquitectura, diagramas y modelo de datos propuesto.
- `poc/`: cuatro pruebas de concepto, que **no equivalen** todavía a la API web final.
