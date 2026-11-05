# Fase 5 · Validación — Clínica ambulatoria

**Grupo 2 · Proyecto A · Persistencia políglota**
**Producto de control:** resultados y evidencias de pruebas funcionales, integrales, de fallos y de consistencia.

La validación se automatizó en 45 pruebas (`tests/`) que se ejecutan contra el sistema levantado con Docker Compose y dejan un informe con fecha en [`docs/evidencias/fase5/`](evidencias/fase5/). Cubren las 5 pruebas de la defensa (§7 del enunciado), los 5 flujos integrales, las reglas RN-01 a RN-15 y las restricciones RS-01 a RS-06.

## 1. Estrategia

| Tipo | Qué demuestra | Archivo | Detiene contenedores |
| --- | --- | --- | --- |
| Funcional | Cada operación de la API cumple su contrato: códigos HTTP, validaciones, permisos y transiciones de estado | `test_flujos.py`, `test_validacion.py` | No |
| Integral | Una operación atraviesa dos o más gestores (MongoDB → outbox → Typesense; MongoDB + PostgreSQL al reservar) | `test_flujos.py`, `test_validacion.py` | No |
| Consistencia | Fuerte en la agenda: concurrencia, idempotencia e invariante cupo ↔ cita. Eventual en la búsqueda: retraso medido y convergencia con la fuente oficial | `test_validacion.py` | No |
| Fallos (P5) | Caída y recuperación de cada gestor y del sincronizador; persistencia tras reiniciar | `test_fallos.py` | **Sí** (`--fallos`) |

Las pruebas usan HTTP contra la API, igual que la web; ninguna escribe directamente en una base de datos. Cada prueba crea su propio catálogo y sus franjas con nombres únicos, así que se pueden repetir sin limpiar datos.

**Las pruebas no ensucian la demostración:** reservan con pacientes exclusivos (`prueba1` a `prueba3`, creados por la semilla), y al terminar la sesión cancelan sus citas y desactivan todo lo que crearon en el catálogo (borrado lógico, sin perder historial). Así la búsqueda, Administración, «Citas pendientes» y las cuentas `paciente1` a `paciente3` quedan como las dejó la semilla.

**Invariante de la agenda** (verificado después de cada prueba de concurrencia): un cupo está `RESERVADO` si y solo si tiene exactamente una cita activa (`PENDIENTE` o `CONFIRMADA`), y nunca más de una.

## 2. Cómo ejecutar

```bash
docker compose up --build -d                 # sistema completo; esperar a que /health diga "ok"
pip install -r tests/requirements.txt

python tests/reporte.py                      # funcionales, integrales y consistencia (~1 min)
python tests/reporte.py --fallos             # además P5: detiene y levanta contenedores (~4 min)
```

Cada ejecución genera `docs/evidencias/fase5/resultados-AAAAMMDD-HHMM.md` (más el XML JUnit). El informe incluye el commit, el estado de los contenedores, las métricas medidas y el resultado de cada prueba. **Ese archivo es la evidencia**: se sube al repositorio.

- En Windows funciona igual desde PowerShell con Docker Desktop abierto (`python` en lugar de `python3`).
- Las pruebas de fallos usan `docker compose stop|start|restart <servicio>`. Si una aserción falla, el servicio detenido se vuelve a levantar igual.
- Para ver el vencimiento real del hold en la demo, poner `TTL_BLOQUEO_CUPO_SEGUNDOS=20` en `.env` y ejecutar `docker compose up -d api`.

## 3. Matriz de pruebas

### 3.1 Pruebas de la defensa (§7 del enunciado)

| Prueba §7 | Pruebas automatizadas | Qué se mide o comprueba |
| --- | --- | --- |
| P1. Registrar profesional y verlo en la búsqueda | `test_p1_…`, **V-01** | Retraso alta → búsqueda, en 5 repeticiones (promedio y máximo) |
| P2. Cambiar especialidad o sede y medir el retraso | `test_p2_…`, **V-02**, **V-03**, **F-06** | Retraso por evento (`retraso_ms` del outbox); la proyección converge con MongoDB |
| P3. Dos reservas del mismo horario | `test_p3_…`, **V-04**, **V-05**, **V-07** | 1 × `201` y el resto `409`; invariante de la agenda |
| P4. Cancelar y volver a reservar | `test_p4_…`, **V-06**, **V-13**, **V-15** | Cupo liberado, historial conservado, sin doble cancelación |
| P5. Reiniciar contenedores; búsqueda caída | **F-01** a **F-07** | Citas persisten; reserva sin búsqueda; recuperación medida |

### 3.2 Pruebas nuevas de la Fase 5

| ID | Tipo | Qué verifica | Requisito | Gestores |
| --- | --- | --- | --- | --- |
| V-01 | Integral · eventual | Profesional nuevo visible en `/buscar` (5 repeticiones, retraso < 15 s) | P1, RN-12 | MongoDB → Typesense |
| V-02 | Integral · eventual | El outbox registra `creado_en`, `procesado_en` y `retraso_ms`; el worker está activo; no expone secretos | P2, HU-14 | MongoDB |
| V-03 | Consistencia eventual | Con el outbox vacío, la búsqueda contiene todos los activos y ningún inactivo | RN-11, RN-12 | MongoDB, Typesense |
| V-04 | Concurrencia | 8 reintentos simultáneos con la misma `Idempotency-Key` → 1 cita | RN-03 | PostgreSQL |
| V-05 | Concurrencia | Reprogramar y reservar el mismo destino a la vez → gana uno; el perdedor no altera nada | RN-01, RN-05 | PostgreSQL |
| V-06 | Concurrencia | Paciente y recepción cancelan a la vez → un `200`, un `409`, un solo evento | RN-04, RN-06 | PostgreSQL |
| V-07 | Concurrencia | 20 reservas simultáneas sobre 4 franjas → exactamente 4 éxitos | RN-01 | PostgreSQL |
| V-08 | Funcional · seguridad | La API ignora el estado enviado, impide reservar a nombre de otro y calcula `fin` con el catálogo | RS-04, RN-10 | PostgreSQL, MongoDB |
| V-09 | Funcional | Entradas inválidas → 404/422 con formato de error uniforme | RS-04, RS-06 | — |
| V-10 | Funcional · seguridad | Matriz de permisos aplicada en la API (10 casos) | RN-14, §2 | — |
| V-11 | Seguridad | Token con firma alterada → `401 TOKEN_INVALIDO` | RS-04 | — |
| V-12 | Funcional | Cita creada por recepción nace `CONFIRMADA`; si el paciente reprograma vuelve a `PENDIENTE` | Fase 4 §2 | PostgreSQL |
| V-13 | Funcional | Una cita cancelada no se confirma ni se reprograma | Máquina de estados | PostgreSQL |
| V-14 | Funcional | Hold: vence según el TTL configurado; otro paciente no puede liberarlo | RN-07 | PostgreSQL |
| V-15 | Consistencia | Reenviar la petición original tras cancelar devuelve la cita cancelada, no crea otra | RN-03, RN-04 | PostgreSQL |
| V-16 | Seguridad | Errores uniformes; ninguna respuesta contiene contraseñas, claves ni cadenas de conexión | RS-03, RS-06 | — |
| V-17 | Funcional | Lecturas públicas del catálogo, la búsqueda y las franjas responden | Pantallas §5 | Todos |
| V-18 | Funcional | `cardiolgia` encuentra Cardiología; filtros y facetas por especialidad y sede | Fase 2 (elección de Typesense) | Typesense |
| V-19 | Funcional | Orden por cercanía: distancias crecientes, la sede más cercana primero; coordenadas inválidas → 422 | H-01, búsqueda por ubicación (§1) | Typesense, MongoDB |
| F-01 | Fallo | `docker compose restart`: citas, historial, catálogo e índice persisten; filtros de Redis se pierden | P5, RN-13 | Todos |
| F-02 | Fallo | Typesense detenido: `/health` degradado, búsqueda `503`, listado desde MongoDB, reserva `201`; el cambio pendiente llega al volver | P5, RN-13 | Typesense, MongoDB |
| F-03 | Fallo | Redis detenido: solo `/sesiones` falla (`503`); reservar y buscar siguen | RN-13 | Redis |
| F-04 | Fallo | PostgreSQL detenido: reserva `503`, `/health` `503`, nada queda a medias; el reintento con la misma clave reserva una vez | RN-02, RS-06 | PostgreSQL |
| F-05 | Fallo | MongoDB detenido: no se reserva sin validar el catálogo (`503`); las citas existentes se consultan | RN-08 | MongoDB |
| F-06 | Fallo | Sincronizador detenido: eventos se acumulan sin perderse, pantalla técnica lo marca inactivo, se aplican al volver | RN-12 | MongoDB, Typesense |
| F-07 | Recuperación | Reindexación completa desde MongoDB reproduce el mismo número de documentos | RN-12 | MongoDB → Typesense |

Las 14 pruebas de la Fase 4 (`test_flujos.py`) siguen en la suite: salud, roles, validaciones de agenda, P1 a P4, idempotencia, reprogramación atómica, transiciones, privacidad, hold, P2, desactivación y TTL de Redis.

## 4. Resultados

### 4.1 Verificación previa (3 de noviembre de 2026)

Antes de entregar la suite, se ejecutó completa en un entorno sin acceso a Docker Hub. PostgreSQL 16 y Redis 7 eran **reales**, con los scripts de `db/postgres/`; MongoDB y Typesense estaban **simulados en memoria** con la misma interfaz que usa la API.

| Resultado | Detalle |
| --- | --- |
| 43 de 44 pasaron | Las 14 de Fase 4, las 23 nuevas (V-01 a V-18; V-17 cuenta 6 rutas) y 6 de las 7 de fallos |
| F-05 omitida | MongoDB simulado no se puede detener; debe ejecutarse con Docker |
| Retraso alta → búsqueda (V-01) | 0,43 s promedio, 0,54 s máximo (en el simulado; con Typesense real se espera 1–3 s) |
| Recuperación tras caída de PostgreSQL (F-04) | 2,4 s hasta `/health` ok, sin intervención |

Esto confirma que las pruebas son correctas y que la lógica de agenda, citas e idempotencia se sostiene con la base de datos real. **No reemplaza la corrida oficial** con los cuatro gestores reales en Docker.

### 4.2 Corrida oficial con Docker Compose

Ejecutada con los cuatro gestores reales, incluidas las pruebas de fallos (`python tests/reporte.py --fallos`).

| Fecha | Commit | Quién ejecutó | Equipo | Resultado | Informe |
| --- | --- | --- | --- | --- | --- |
| 05/11/2026 10:55 | `8052758` | Nicolás García | Windows 11 · Docker 29.8.0 | **44 pasaron · 0 fallaron · 0 omitidas** (247 s) | [resultados-20261105-1055.md](evidencias/fase5/resultados-20261105-1055.md) |
| 05/11/2026 10:54 | `8052758` | Nicolás García | Windows 11 · Docker 29.8.0 | 37 pasaron · 7 omitidas (sin `--fallos`) | [resultados-20261105-1054.md](evidencias/fase5/resultados-20261105-1054.md) |

| Métrica | Valor medido |
| --- | --- |
| Retraso alta → búsqueda (V-01, 5 repeticiones) | 0,98 s promedio · 1,09 s máximo |
| Retraso cambio de sede → búsqueda (P2) | 0,93 s |
| Recuperación tras `docker compose restart` (F-01) | 10,0 s |
| Recuperación de la búsqueda tras levantar Typesense (F-02) | 3,3 s |
| Recuperación de la agenda tras levantar PostgreSQL (F-04) | 5,7 s |

La corrida oficial confirmó la verificación previa y completó la única prueba que faltaba (F-05, caída de MongoDB).

## 5. Evidencias para la defensa (capturas)

El informe automático demuestra el comportamiento de la API. Para la web, el entregable 8 pide capturas o video. Guardarlas en `docs/evidencias/fase5/capturas/` con estos nombres:

| Archivo | Pantalla | Qué se ve | Estado |
| --- | --- | --- | --- |
| `01-busqueda.png` | Inicio | Resultados con la etiqueta de origen Typesense y el tiempo | ✅ |
| `03a-detalle-franjas.png` | Detalle del profesional | Ficha leída de MongoDB y franjas leídas de PostgreSQL | ✅ |
| `03b-franja-apartada.png` | Detalle (Paciente 1) | Hold de 5 min sobre la franja, con la clave de idempotencia | ✅ |
| `03-reserva-ok.png` | Detalle del profesional | Confirmación con el ID de la cita y la franja tachada | ✅ |
| `04-reserva-bloqueada.png` | Detalle (Paciente 2, otra ventana) | La franja apartada por Paciente 1 aparece «bloqueado» y no se puede tomar (P3) | ✅ |
| `05-mis-citas-historial.png` | Mis citas | Cita cancelada, franja liberada e historial CREADA → CANCELADA (P4, RN-04) | ✅ |
| `06a-recepcion-reservar-para.png` | Recepción | Abre en el próximo día con agenda; reservar para un paciente | ✅ |
| `06-recepcion-reprogramar.png` | Recepción | «Cita movida de #1 a #20 (confirmada)» y la franja #1 libre de nuevo (flujo 4) | ✅ |
| `06b-historial-reprogramada.png` | Mis citas (Paula) | Historial CREADA → REPROGRAMADA #1 → #20 con actor y hora (RN-06) | ✅ |
| `07-busqueda-degradada.png` | Inicio con Typesense detenido | Aviso de buscador no disponible y listado desde MongoDB (P5, RN-13) | ✅ |
| `08-tecnico-pendientes.png` | Técnico con Typesense detenido | Causa del error del sincronizador y 18 eventos pendientes que no se pierden | ✅ |
| `10-reinicio.png` | Mis citas (Pablo) tras `docker compose restart` | La cita confirmada en la #14, su historial de reprogramación y la cancelada siguen intactas (P5) | ✅ |

Tres comportamientos no tienen captura porque ya los respalda la evidencia automatizada:

- **Orden por cercanía:** prueba V-19.
- **Alta de profesional y su sincronización a la búsqueda:** pruebas P1, V-01 y V-02, con el retraso medido en la corrida oficial (~1 s).
- **Gestores sin puertos publicados (RS-02):** la salida de `docker compose ps` incluida en [resultados-20261105-1055.md](evidencias/fase5/resultados-20261105-1055.md), donde solo la API publica puerto (`127.0.0.1:8000`).

La persistencia tras `docker compose restart` (P5) también la demuestra la prueba automatizada **F-01** de la corrida oficial (10,0 s de recuperación, cita e historial intactos). Ojo: `docker compose down -v` sí borra los datos, porque elimina los volúmenes.

## 6. Hallazgos de la validación

Las pruebas no encontraron errores de lógica en la API: concurrencia, idempotencia, permisos y recuperación se comportaron según el diseño. Sí aparecieron diferencias entre lo documentado y lo implementado, y un error de configuración. Todos quedaron corregidos salvo H-05, que es una decisión de diseño.

| ID | Hallazgo | Impacto | Acción propuesta |
| --- | --- | --- | --- |
| H-01 | La Fase 2 eligió Typesense, entre otras razones, por el **orden por cercanía** (`geopoint`), y el enunciado pide buscar por **ubicación**. `/buscar` no tiene campo geográfico ni parámetro de cercanía, aunque las sedes ya guardan coordenadas en MongoDB | Medio: la defensa puede preguntar por una ventaja que se usó para justificar el gestor pero no se implementó | **Corregido**: `ubicaciones: geopoint[]` en la proyección, `cerca=lat,lon` en `/buscar`, opción «Más cercanas» en la web y prueba V-19 |
| H-02 | Con Typesense caído, el sincronizador falla en `asegurar_coleccion()` antes de procesar el lote: los eventos quedan pendientes, pero `intentos` y `ultimo_error` no se actualizan y no hay backoff. La Fase 4 y `tests/README.md` describen backoff de hasta 60 s | Bajo: el comportamiento real es mejor (reintenta cada ~5 s), pero la pantalla técnica muestra "0 con error" durante la caída | **Corregido**: el worker guarda `ultimo_error` en su latido y la pantalla Técnico lo muestra mientras dure la caída |
| H-03 | Permisos: la Fase 1 dice que solo recepción confirma citas y solo el administrador ve el sincronizador; la implementación permite ambos a recepción y administración. `api-contratos.md` dice "Recepción" para confirmar y la Fase 4 dice "Recepción, Admin" | Bajo | **Corregido**: la matriz de la Fase 1 y `api-contratos.md` quedaron iguales a la API |
| H-04 | `api-contratos.md` y `fase3-diagramas-pantallas.md` aún dicen "decisión pendiente" o "provisional" (identidad, reprogramación, holds, `/health`), aunque la Fase 4 ya las cerró. La Fase 3 propone `/sesiones/{id}/filtros` y claves `filtros:{sesion_id}`; lo implementado es `/sesiones/filtros` y `sesion:{usuario}:filtros` | Bajo: confunde a quien lea el informe | **Corregido**: nota de actualización en esas secciones y textos provisionales reemplazados por la decisión implementada |
| H-06 | `db/mongo/01_init.js` tenía etiquetas `<escape>` sobrantes al inicio y al final: `mongo-init` terminaba con error y la API no arrancaba con Docker | **Alto** (bloqueaba el despliegue) | **Corregido** en esta fase (commit `9c9afd4`) |
| H-07 | La Fase 2 (decisión D-08) dice que Redis usa persistencia RDB, pero `docker-compose.yml` la desactiva (`--save ""`) | Bajo: lo implementado es lo correcto, Redis es efímero por diseño | **Corregido** en la Fase 2 (D-08) |
| H-05 | El enunciado menciona búsqueda por **horario**. La búsqueda no filtra por horario; las franjas se ven en la ficha del profesional (desde PostgreSQL) | Bajo: es coherente con no poner disponibilidad en el índice (RN-02) | Decisión de diseño: se mantiene y se explica en la defensa (la ficha ya muestra las franjas de 14 días por fecha) |

## 7. Límites de la validación

- Concurrencia probada hasta 20 peticiones simultáneas desde un solo equipo. Demuestra la regla de exclusión, no el rendimiento bajo carga real.
- El vencimiento del hold se verifica por su hora de vencimiento y por código (estado efectivo `BLOQUEADO` → `DISPONIBLE`), sin esperar los 300 s. Para verlo en vivo, usar el TTL corto de la sección 2.
- La ventana entre validar el catálogo (MongoDB) y reservar (PostgreSQL) está documentada en la Fase 4 y no se prueba: requiere desactivar un profesional en el mismo milisegundo.
- No se prueba la web con un navegador automatizado; su comportamiento se respalda con las capturas de la sección 5.
- Typesense (versiones 0.24 a 30.0) se cierra con SIGSEGV al iniciar en un equipo con Intel Core Ultra 7 258V y WSL2 6.18, incluso con `vsyscall=emulate`. El resto de gestores funciona en ese equipo; la corrida oficial se ejecutó en otro.