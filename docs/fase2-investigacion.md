# Fase 2 · Investigación — Selección de gestores

Grupo 2 · Daniel Guevara, Nicolás García, Camila Barrionuevo · 31 de octubre de 2026

Producto de control: **matriz argumentada de selección**.

Esta fase valida (o corrige) los gestores propuestos en la [Fase 1](fase1-comprension.md). Para cada dominio se comparan al menos dos candidatos con fuentes técnicas, se elige uno, se justifica el descartado y se deja una **prueba pequeña** ejecutable en [`poc/`](../poc/README.md).

## 1. Método

1. Se parte de las necesidades de cada dominio definidas en la Fase 1: patrón de acceso, estructura, consistencia, volumen y disponibilidad.
2. Por dominio se evalúan dos o tres candidatos con los criterios de la tabla siguiente, en escala de 1 (malo) a 5 (muy bueno).
3. Cada criterio tiene un peso distinto según el dominio; por ejemplo, en Agenda la consistencia pesa el triple.
4. La decisión final no es solo la suma: se revisa si el ganador obliga a algo inaceptable, como una transacción entre gestores.
5. Cada elección se comprueba con una prueba pequeña reproducible.

| Criterio | Qué se pregunta |
| --- | --- |
| Ajuste al modelo | ¿La forma natural del dato encaja con el modelo del gestor? |
| Consistencia | ¿Garantiza lo que el dominio exige (atomicidad, unicidad, aislamiento)? |
| Consultas dominantes | ¿Resuelve bien las consultas más frecuentes del dominio? |
| Operación en Docker | ¿Es fácil de levantar, persistir y recuperar con Compose en un portátil de laboratorio? |
| Recursos | ¿Cuánta RAM y CPU consume en la demo? |
| Conocimiento del equipo | ¿Lo podemos explicar y depurar en la defensa? |

## 2. Necesidades por dominio

| Dominio | Patrón de acceso dominante | Estructura del dato | Consistencia requerida | Volumen estimado (demo / 1 año) | Disponibilidad |
| --- | --- | --- | --- | --- | --- |
| Catálogo | Muchas lecturas de ficha completa, pocas escrituras del administrador | Documento con atributos variables (idiomas, sedes, modalidades, horarios de referencia) | Fuerte en su propia escritura; el resto la lee | ~5 sedes, ~15 especialidades, ~200 profesionales, ~100 servicios | Alta para leer fichas; si cae, no se puede reservar (RN-08) |
| Agenda y cupos | Escrituras concurrentes sobre la **misma fila** (el cupo), lecturas por profesional, sede y rango de fechas | Tabular: cupo = profesional + sede + servicio + intervalo + estado | **Fuerte y crítica**: un cupo, una cita activa | ~200 prof. × 16 cupos/día × 60 días ≈ 190 000 cupos | Crítica: si cae, no se reserva |
| Citas | Reserva, cambio de estado, historial por paciente | Tabular y relacional con Cupo; eventos inmutables | **Fuerte**, y atómica junto con el cupo | ~150 000 citas/año, ~3 eventos por cita | Crítica |
| Búsqueda | Solo lectura: texto libre, filtros combinados, orden por cercanía | Documento plano y desnormalizado | Eventual (retraso de segundos aceptable) | ~200–300 documentos | Degradable: si cae, se informa y se sigue reservando |
| Sesiones y preferencias | Lectura y escritura por clave de sesión, con vencimiento | Clave → valor u hash pequeño | Efímera; se acepta perderla | Cientos de claves activas | Degradable: si cae, se pierden borradores, no citas |

## 3. Matriz comparativa por dominio

### 3.1 Agenda y cupos + Citas

Se evalúan juntos porque la Fase 1 exige que **cupo + cita + evento** cambien en una sola transacción (RN-01, RN-04, RN-05).

| Criterio (peso) | PostgreSQL | MongoDB | Redis |
| --- | --- | --- | --- |
| Ajuste al modelo (×2) | 5 — filas con claves foráneas cupo ↔ cita | 3 — se puede, pero la relación queda a cargo de la aplicación | 2 — sin relaciones ni consultas por rango cómodas |
| Consistencia (×3) | 5 — ACID; bloqueo por fila; índice único parcial; restricción de exclusión contra solapes | 3 — atómico por documento; varios documentos requieren transacción y replica set; sin restricción nativa de no solapamiento | 2 — operaciones atómicas por clave, pero la persistencia no es su punto fuerte y no hay restricciones declarativas |
| Consultas dominantes (×2) | 5 — rangos de fecha con índice B-tree, joins con citas | 4 — índices compuestos | 2 — habría que mantener índices manuales |
| Operación en Docker (×1) | 5 | 4 — transacciones exigen replica set | 5 |
| Recursos (×1) | 5 | 4 | 5 |
| Conocimiento del equipo (×1) | 5 | 4 | 3 |
| **Total ponderado** | **50** | **35** | **25** |

**Elegido: PostgreSQL.**

- **Doble reserva concurrente:** un `UPDATE cupo SET estado='RESERVADO' WHERE id=$1 AND estado='DISPONIBLE'` es atómico. PostgreSQL bloquea la fila que se actualiza, y la segunda transacción espera y vuelve a evaluar la condición. Solo una encuentra el cupo `DISPONIBLE`. Es el mismo mecanismo que documenta PostgreSQL para `UPDATE` y `SELECT ... FOR UPDATE`: otra transacción no puede modificar esas filas hasta que la primera confirma o revierte.
- **Segunda barrera:** un índice único parcial `UNIQUE (cupo_id) WHERE estado IN ('PENDIENTE','CONFIRMADA')` impide dos citas activas para el mismo cupo, aunque la aplicación tenga un error.
- **Sin solapes (RN-09):** `EXCLUDE USING gist (profesional_id WITH =, tstzrange(inicio, fin) WITH &&)` con la extensión `btree_gist` rechaza cupos solapados del mismo profesional directamente en la base de datos.
- **Idempotencia (RN-03):** `UNIQUE (clave_idempotencia)` en `cita`. Un reintento choca con la restricción y la API devuelve la cita existente.
- **Reprogramar (RN-05):** liberar el cupo viejo, ocupar el nuevo, cambiar la cita e insertar el evento ocurren en una sola transacción local. Si algo falla, nada cambia.

**Descartado: MongoDB para agenda.** Podría reservar un cupo con `findOneAndUpdate` de forma atómica, porque en MongoDB una operación sobre un solo documento es atómica. Pero reservar y reprogramar tocan varios documentos (dos cupos, una cita y un evento), y eso obliga a transacciones multi-documento. Además, la no superposición de horarios tendría que programarse en la aplicación. PostgreSQL da las tres garantías de forma declarativa.

**Descartado: Redis para agenda.** El enunciado prohíbe que la disponibilidad dependa de una caché (§6, §12). Redis no ofrece restricciones declarativas, y su persistencia en disco es configurable pero no es su modelo principal.

### 3.2 Catálogo de servicios y profesionales

| Criterio (peso) | MongoDB | PostgreSQL (JSONB) |
| --- | --- | --- |
| Ajuste al modelo (×3) | 5 — la ficha es un documento con arreglos y subdocumentos variables | 4 — columnas fijas más un JSONB para lo variable |
| Consistencia (×1) | 4 — atómico por documento; validación con `$jsonSchema` | 5 |
| Consultas dominantes (×2) | 5 — leer la ficha completa en una sola lectura | 4 — requiere joins o JSONB |
| Operación en Docker (×1) | 4 — replica set de un nodo para la outbox transaccional | 5 |
| Recursos (×1) | 4 | 5 — reutiliza la instancia de agenda |
| Aislamiento de fallos (×1) | 5 — un problema de catálogo no afecta a la base de agenda | 3 — comparte instancia con lo crítico |
| **Total ponderado** | **41** | **37** |

**Elegido: MongoDB.**

- Los atributos son realmente variables por profesional (idiomas, varias sedes, modalidades, horarios de referencia por sede, certificaciones opcionales). El documento se guarda y se lee tal como lo muestra la ficha.
- El validador `$jsonSchema` obliga a tener los campos mínimos (`nombre`, `especialidad_ids`, `activo`) sin cerrar la puerta a atributos nuevos.
- **Outbox atómica:** el cambio del profesional y el evento para el sincronizador se escriben en una **transacción multi-documento**. MongoDB solo permite transacciones en replica set (o clúster con sharding); un servidor standalone puede convertirse en replica set de un solo nodo arrancando `mongod` con `--replSet`. Por eso el Compose levantará `rs0` con un nodo.
- Separa la carga y los fallos del catálogo (escrituras del administrador, cambios de esquema) de la base crítica de agenda.

**Descartado: PostgreSQL con JSONB.** Es una alternativa seria y la dejamos documentada: reduciría un gestor y daría transacción conjunta catálogo + agenda. La descartamos porque (1) la ficha variable es el caso típico de documento, (2) mezclaría en la misma instancia la carga de administración con la de reservas, y (3) el enunciado pide distinguir el catálogo oficial de su proyección y demostrar una sincronización entre gestores. Con MongoDB → Typesense esa sincronización es real y medible. **Contraargumento para la defensa:** si el requisito cambiara a “minimizar gestores”, el catálogo sería el primero en moverse a PostgreSQL con JSONB.

### 3.3 Búsqueda

| Criterio (peso) | Typesense | Elasticsearch | PostgreSQL (full-text + pg_trgm) |
| --- | --- | --- | --- |
| Ajuste al modelo (×2) | 5 — documentos planos con facetas | 5 | 3 — `tsvector` y trigramas sobre tablas |
| Consultas dominantes (×3) | 5 — texto con tolerancia a errores, filtros, facetas y orden geográfico en una sola API | 5 — muy potente, con más configuración | 3 — sin facetas nativas; la tolerancia a errores requiere `pg_trgm` |
| Operación en Docker (×2) | 5 — un binario, una imagen | 2 — JVM, `vm.max_map_count`, seguridad TLS en 8.x | 5 |
| Recursos (×2) | 5 — ~100 MB para este volumen | 2 — 1–2 GB de heap como mínimo | 5 |
| Aislamiento de la carga crítica (×1) | 5 | 5 | 2 — compite con las reservas en la misma base |
| **Total ponderado** | **50** | **37** | **35** |

**Elegido: Typesense.**

- Tolerancia a errores tipográficos activa por defecto. El paciente escribe “cardiolgia” y encuentra Cardiología.
- Filtros (`filter_by`), facetas (`facet_by`) y orden por cercanía con `geopoint`, que cubren “especialidad, sede, texto y ubicación”.
- Mantiene en memoria los campos de búsqueda y filtrado; con unos cientos de documentos el consumo es mínimo. Si cae, el índice se **reconstruye completo desde MongoDB** (RN-12).
- Es una **proyección**: nunca decide disponibilidad (RS-05).

**Descartado: Elasticsearch.** Resuelve lo mismo, pero es una plataforma pensada también para analítica y observabilidad. Necesita una JVM con al menos 1–2 GB de heap, ajustes del kernel y TLS por defecto. Es demasiado peso operativo para ~300 documentos en un portátil de laboratorio.

**Descartado: búsqueda en PostgreSQL.** Evitaría un gestor, pero pondría la carga de búsqueda (la más frecuente) sobre la misma base que decide las reservas. Además, no da facetas ni tolerancia a errores de forma nativa.

### 3.4 Sesiones y preferencias

| Criterio (peso) | Redis | PostgreSQL (tabla con `expira_en`) | Memcached |
| --- | --- | --- | --- |
| Ajuste al modelo (×2) | 5 — clave → hash con TTL | 3 | 4 — solo cadenas |
| Vencimiento automático (×3) | 5 — `EXPIRE` nativo, borrado automático | 2 — requiere un job de limpieza | 5 |
| Operación en Docker (×1) | 5 | 5 | 5 |
| Recursos (×1) | 5 | 4 | 5 |
| Estructuras útiles (×1) | 5 — hash, set, contadores | 3 | 1 |
| **Total ponderado** | **40** | **24** | **35** |

**Elegido: Redis.**

- `SET ... EX` y `EXPIRE` asocian un tiempo de vida a cada clave; al vencer, Redis la elimina sola. Cubre “crear, recuperar y caducar estado temporal”.
- Hash para filtros guardados y borradores (`HSET borrador:{sesion} profesional_id … servicio_id … cupo_id …`).
- Si Redis cae, el usuario pierde filtros y borradores, pero ninguna cita (RN-13). La web lo informa y sigue funcionando.

**Descartado: PostgreSQL.** Funcionaría, pero añade escrituras constantes de bajo valor a la base crítica y necesita un proceso de limpieza para simular el TTL.

**Descartado: Memcached.** Tiene TTL, pero no ofrece hashes ni persistencia opcional, y el equipo ya conoce Redis.

> **Decisión importante:** el **bloqueo temporal de cupo (hold) de 5 minutos no vive en Redis**, porque es parte de la disponibilidad y debe decidirse en el registro autoritativo (RN-02). Se guarda en PostgreSQL como `cupo.bloqueado_hasta`. Redis solo guarda el borrador del formulario.

## 4. Matriz resumen de selección

| Dominio | Patrón de acceso | Estructura | Consistencia | Volumen | Disponibilidad | **Gestor elegido** | Alternativa descartada | Justificación breve |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Catálogo | Lectura de ficha completa; pocas escrituras | Documento variable | Fuerte local | Cientos | Alta | **MongoDB 7 (replica set de 1 nodo)** | PostgreSQL JSONB | Ficha variable como documento; validación `$jsonSchema`; outbox en transacción; separa la carga de la agenda |
| Agenda y cupos | Escritura concurrente sobre la misma fila; rangos de fecha | Tabular | **Fuerte (crítica)** | ~190 000 | Crítica | **PostgreSQL 16** | MongoDB, Redis | Bloqueo por fila, índice único parcial, restricción de exclusión contra solapes |
| Citas | Reserva, cambio de estado, historial | Relacional + eventos | **Fuerte** | ~150 000/año | Crítica | **PostgreSQL 16** (misma base que agenda) | MongoDB | Cupo + cita + evento en **una** transacción local; idempotencia con `UNIQUE` |
| Búsqueda | Texto, filtros, facetas, cercanía | Documento plano | Eventual | Cientos | Degradable | **Typesense** | Elasticsearch, PostgreSQL FTS | Tolerancia a errores y geo por defecto; ligero; reconstruible desde el catálogo |
| Sesiones | Clave → valor con vencimiento | Hash | Efímera | Cientos | Degradable | **Redis 7** | PostgreSQL, Memcached | TTL nativo; perderlo no afecta al negocio |

Resultado: **se confirman los cuatro gestores propuestos en la Fase 1**. La única corrección es que el hold de cupo pasa explícitamente a PostgreSQL.

## 5. Pregunta de investigación (§8)

> ¿Qué operaciones requieren consistencia fuerte y cuáles pueden tolerar consistencia eventual? Comparen dos alternativas de persistencia para agenda y búsqueda y presenten el motivo por el que descartan una.

### 5.1 Fuerte vs. eventual

| Operación | Consistencia | Motivo | Qué pasa si se relaja |
| --- | --- | --- | --- |
| Reservar cupo | **Fuerte** | Dos pacientes no pueden obtener el mismo cupo | Doble reserva: fallo grave de negocio |
| Cancelar y liberar cupo | **Fuerte** | Cita y cupo deben cambiar juntos | Cupo “fantasma” ocupado o cita activa sin cupo |
| Reprogramar | **Fuerte** | Liberar y ocupar es una unidad | Paciente con dos cupos o con ninguno |
| Confirmar cita (recepción) | **Fuerte** | Cambia el estado autoritativo | Estado contradictorio entre pantallas |
| Hold de 5 minutos | **Fuerte** | Forma parte de la disponibilidad | Dos usuarios llenan el formulario del mismo cupo y uno falla al final |
| Crear o editar profesional | Fuerte **local** (en MongoDB) | Es la fuente oficial | — |
| Ver la ficha en la búsqueda | **Eventual** | Es una proyección; segundos de retraso no rompen nada | El paciente ve el dato viejo unos segundos; al reservar, la API valida contra el catálogo |
| Listar resultados de búsqueda | **Eventual** | Igual que el anterior | Un profesional recién desactivado aparece unos segundos y la reserva lo rechaza (RN-08) |
| Mostrar franjas en el detalle | **Fuerte en lectura** | Se lee de PostgreSQL, no del índice (HU-06) | — |
| Filtros y borrador | **Efímera** | Se puede volver a elegir | Se pierde comodidad, no datos |

### 5.2 Agenda: PostgreSQL vs. MongoDB → se descarta MongoDB

Con MongoDB, reservar un único cupo sería atómico (`findOneAndUpdate` con `{estado: "DISPONIBLE"}` en el filtro). Pero **reprogramar** toca dos cupos, una cita y un evento, y **cancelar** toca cupo, cita y evento. Eso exige transacciones multi-documento sobre replica set, y la regla de no solapamiento quedaría en código de la aplicación. PostgreSQL resuelve las tres reglas críticas (RN-01, RN-05, RN-09) **con restricciones declarativas** que la base garantiza aunque la API tenga un error. Por eso se descarta MongoDB para agenda.

### 5.3 Búsqueda: Typesense vs. Elasticsearch → se descarta Elasticsearch

Ambos cubren texto, filtros, facetas y geo. Para ~300 documentos, Elasticsearch añade una JVM con 1–2 GB de heap, ajuste de `vm.max_map_count` y gestión de TLS, sin aportar nada que este caso necesite (sus fortalezas son la analítica y los volúmenes grandes en disco). Typesense entrega tolerancia a errores y orden geográfico por defecto, con un solo binario ligero. Su límite, que el índice de búsqueda debe caber en RAM, no aplica a este volumen. Por eso se descarta Elasticsearch.

## 6. ¿Por qué cuatro gestores y no tres?

El enunciado pide **al menos tres** y advierte que no se premia usar más sin necesidad. Cada gestor responde a una necesidad que los otros cubren mal:

| Si quitamos… | Lo absorbería… | Costo |
| --- | --- | --- |
| MongoDB | PostgreSQL (JSONB) | Viable. Es el candidato a eliminar si se pide simplificar (ver 3.2) |
| Typesense | PostgreSQL | La carga de búsqueda pasa a la base crítica; sin tolerancia a errores ni facetas nativas |
| Redis | PostgreSQL | Job de limpieza y escrituras de bajo valor en la base crítica |
| PostgreSQL | MongoDB | Se pierden las restricciones declarativas de la agenda. **No negociable** |

## 7. Decisiones técnicas para las Fases 3 y 4

| # | Decisión | Origen |
| --- | --- | --- |
| D-01 | MongoDB arranca como **replica set `rs0` de un nodo** (`--replSet rs0` + `rs.initiate`). Con autenticación activada, el replica set necesita además un `keyFile`. | Transacciones de la outbox (3.2) |
| D-02 | Patrón **outbox**: colección `outbox` en MongoDB escrita en la misma transacción que el cambio del catálogo. El sincronizador la lee en orden, hace upsert en Typesense y marca `procesado_en`. | RN-12, flujo 5 |
| D-03 | Reserva con `UPDATE ... WHERE estado='DISPONIBLE' RETURNING` dentro de la transacción; 0 filas significa **409**. | RN-01, flujo 3 |
| D-04 | Índice único parcial `uq_cita_activa_por_cupo` y `UNIQUE (clave_idempotencia)`. | RN-01, RN-03 |
| D-05 | `EXCLUDE USING gist` con `btree_gist` para impedir cupos solapados del mismo profesional. | RN-09 |
| D-06 | Hold de cupo como `cupo.bloqueado_hasta` en PostgreSQL, **no** en Redis. | RN-02, RN-07 |
| D-07 | Typesense se puede **reindexar completo** desde MongoDB con un comando del sincronizador. | RN-12, prueba P5 |
| D-08 | Redis con persistencia RDB por defecto; perder datos de Redis es aceptable. Todas las claves llevan TTL. | RN-13 |
| D-09 | Imágenes previstas: `postgres:16-alpine`, `mongo:7.0`, `typesense/typesense:28.0`, `redis:7.4-alpine`. | Despliegue |
| D-10 | Solo la API y la web publican puertos; los gestores quedan en la red interna (en las pruebas pequeñas se publican solo en `127.0.0.1`). | RS-02 |

## 8. Pruebas pequeñas (prueba de concepto)

Están en [`poc/`](../poc/README.md). Se levantan los cuatro gestores con `docker compose -f poc/docker-compose.poc.yml up -d` y se ejecuta un script por gestor.

| Script | Qué demuestra | Resultado esperado |
| --- | --- | --- |
| `01_postgres_concurrencia.py` | 20 hilos reservan el mismo cupo a la vez; idempotencia; cupo solapado; cancelar y volver a reservar | 1 éxito y 19 rechazos (409); un reintento devuelve la misma cita; el solape se rechaza; tras cancelar se puede reservar de nuevo |
| `02_mongo_catalogo_outbox.py` | Fichas con atributos distintos; validador `$jsonSchema`; transacción profesional + outbox; aborto sin efectos | Se insertan fichas heterogéneas; una ficha sin `nombre` se rechaza; al abortar no queda ni el profesional ni el evento |
| `03_typesense_busqueda.py` | Búsqueda con error tipográfico, filtros por especialidad y sede, orden por cercanía; retraso de actualización | “cardiolgia” encuentra Cardiología; el filtro y el orden funcionan; se mide el retraso en ms |
| `04_redis_ttl.py` | Borrador y filtros con TTL; recuperación antes del vencimiento; desaparición después | Antes del TTL se recuperan; después, `None` |

**Resultados obtenidos** (completar al ejecutar y guardar captura en `docs/evidencias/fase2/`):

| Prueba | Resultado | Fecha | Quién la ejecutó |
| --- | --- | --- | --- |
| PostgreSQL – concurrencia | Prueba previa: 20 simultáneas → 1 × 201 y 19 × 409; idempotencia, solape y cancelación OK. Repetir con Docker | 31/10/2026 | |
| MongoDB – outbox | Pendiente de ejecutar con Docker | | |
| Typesense – búsqueda | Pendiente de ejecutar con Docker | | |
| Redis – TTL | Prueba previa: recupera antes del TTL y desaparece después. Repetir con Docker | 31/10/2026 | |

## 9. Fuentes

- PostgreSQL — *Explicit Locking*, bloqueos a nivel de fila: https://www.postgresql.org/docs/current/explicit-locking.html
- PostgreSQL — *Exclusion constraints* (`CREATE TABLE ... EXCLUDE`): https://www.postgresql.org/docs/current/sql-createtable.html
- PostgreSQL — *Partial indexes*: https://www.postgresql.org/docs/current/indexes-partial.html
- MongoDB — *Transactions*: https://www.mongodb.com/docs/manual/core/transactions/
- MongoDB Community — por qué las transacciones requieren replica set: https://www.mongodb.com/community/forums/t/why-replica-set-is-mandatory-for-transactions-in-mongodb/9533
- MongoDB — *Schema validation*: https://www.mongodb.com/docs/manual/core/schema-validation/
- Typesense — *Typesense vs Elasticsearch*: https://typesense.org/typesense-vs-elasticsearch/
- Self-hosting comparison — *Typesense vs Elasticsearch*: https://dev.to/selfhostingsh/typesense-vs-elasticsearch-compared-30kb
- Redis — comando `EXPIRE`: https://redis.io/commands/expire/
