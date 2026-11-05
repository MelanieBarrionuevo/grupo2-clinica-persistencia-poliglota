# Clínica ambulatoria · Persistencia políglota

Proyecto final del I Parcial de **Persistencia políglota** — Proyecto A, **Caso 1: Gestión de citas, disponibilidad y búsqueda de servicios**.

Sistema web para una red de consultorios ficticia: los pacientes buscan servicios y profesionales, reservan citas y siguen su estado; recepción gestiona la agenda y el administrador mantiene el catálogo. El sistema evita reservas duplicadas y ofrece búsquedas ágiles por especialidad, sede, texto y horario.

> Todos los datos son ficticios. No se implementa historia clínica, diagnóstico ni pagos.

## Integrantes · Grupo 2

| Integrante |
| --- |
| Daniel Guevara |
| Nicolás García |
| Camila Barrionuevo |

## Arquitectura propuesta

```
Usuario → Web → API REST → módulos de dominio → gestores
```

| Dominio | Gestor propuesto | Consistencia |
| --- | --- | --- |
| Catálogo (profesionales, servicios, sedes, especialidades) | MongoDB | Fuente oficial |
| Agenda y cupos | PostgreSQL | Fuerte |
| Citas e historial | PostgreSQL | Fuerte |
| Búsqueda (proyección del catálogo) | Typesense | Eventual |
| Sesiones, filtros y borradores | Redis (TTL) | Efímera |

La selección está justificada en la Fase 2: [docs/fase2-investigacion.md](docs/fase2-investigacion.md) (matriz comparativa, alternativas descartadas y pruebas pequeñas en [`poc/`](poc/README.md)). El análisis del caso está en [docs/fase1-comprension.md](docs/fase1-comprension.md).

## Stack tecnológico

| Capa | Tecnología | Motivo |
| --- | --- | --- |
| API REST | FastAPI (Python) | OpenAPI automático en `/docs`, validación con Pydantic, sin atarse a un solo gestor |
| Web | React + Vite | Ligero para 6 pantallas con formularios reales |
| Sincronizador | Python (worker) | Reutiliza modelos y clientes de la API |
| Contenedores | Docker Compose | Gestores, API, sincronizador y web en red interna con volúmenes |

| Gestor | Cliente en la API |
| --- | --- |
| PostgreSQL | `psycopg` 3 |
| MongoDB | `pymongo` |
| Typesense | `typesense` (cliente oficial) |
| Redis | `redis-py` |

## Estructura del repositorio

```
.
├── api/            API REST en FastAPI (módulos de dominio)
├── web/            Interfaz web en React + Vite
├── sync/           Sincronizador catálogo → búsqueda (outbox, Python)
├── db/
│   ├── postgres/   Esquema SQL de agenda y citas
│   ├── mongo/      Inicialización del catálogo
│   └── seed/       Datos semilla de demostración
├── tests/          Pruebas funcionales y de concurrencia
├── poc/            Pruebas pequeñas de la Fase 2 (una por gestor)
├── docs/
│   ├── fase1-comprension.md … fase5-validacion.md
│   ├── api-contratos.md, openapi.json
│   ├── diagramas/
│   ├── evidencias/ Pruebas de concepto (fase2) e informes y capturas de validación (fase5)
│   └── enunciado/
├── .env.example
└── docker-compose.yml
```

## Avance por fases

| Fase | Producto de control | Estado |
| --- | --- | --- |
| 1. Comprensión | Mapa de dominio y flujos | Completada |
| 2. Investigación | Matriz argumentada de selección de gestores | Completada |
| 3. Diseño | Modelo de datos, contratos de API, diagramas | Completada |
| 4. Implementación | Repositorio ejecutable con Docker Compose | Completada · [docs/fase4-implementacion.md](docs/fase4-implementacion.md) |
| 5. Validación | Pruebas y evidencias | Completada · 44/44 pruebas con Docker · [docs/fase5-validacion.md](docs/fase5-validacion.md) |
| 6. Defensa | Exposición y memoria técnica | Lista · guion en [docs/fase6-defensa.md](docs/fase6-defensa.md) |

## Ejecución

### Requisitos previos

- Docker Desktop (o Docker Engine) con Docker Compose v2.
- Puertos libres en el equipo: `3000` (web) y `8000` (API). Las bases de datos no publican puertos.
- Opcional, para las pruebas automatizadas: Python 3.11+.

### Arranque limpio

```bash
cp .env.example .env          # cambiar los valores ficticios si se desea
docker compose up --build -d
docker compose ps             # mongo-init y seed terminan con "Exited (0)": es lo esperado
```

La primera vez tarda unos minutos (descarga de imágenes y build de la web). Al terminar:

| Qué | URL |
| --- | --- |
| Web | http://localhost:3000 |
| API · documentación OpenAPI | http://localhost:8000/docs |
| Salud | http://localhost:8000/api/v1/health |

### Usuarios de demostración (ficticios)

Contraseña de todos: **`Demo123!`** (también hay botones de acceso rápido en la pantalla de login).

| Usuario | Rol |
| --- | --- |
| `paciente1`, `paciente2`, `paciente3` | Paciente |
| `prueba1`, `prueba2`, `prueba3` | Paciente reservado para las pruebas automatizadas (no usar en la demo) |
| `recepcion` | Recepcionista |
| `admin` | Administrador |

La semilla crea 4 especialidades, 3 sedes, 4 servicios, 5 profesionales y franjas para los próximos 5 días hábiles. Es idempotente: no duplica ni borra.

### Reinicio y limpieza

```bash
docker compose restart                 # reinicia todo; citas y catálogo se conservan
docker compose down                    # detiene y elimina contenedores; LOS DATOS SE CONSERVAN (volúmenes)
docker compose up -d                   # vuelve a levantar con los mismos datos
docker compose run --rm seed           # vuelve a sembrar lo que falte (p. ej. nuevas franjas futuras)

# ⚠️ Borrado TOTAL de datos (citas, catálogo e índice). Solo si se quiere empezar de cero:
docker compose down -v
```

`db/postgres/*.sql` solo se aplica cuando el volumen de PostgreSQL está vacío (primer arranque o tras `down -v`).

### Pruebas

```bash
pip install -r tests/requirements.txt
python tests/reporte.py              # suite completa + informe en docs/evidencias/fase5/
python tests/reporte.py --fallos     # además P5: detiene y levanta contenedores
```

Matriz de pruebas y resultados: [docs/fase5-validacion.md](docs/fase5-validacion.md). Comandos para inspeccionar cada gestor: [tests/README.md](tests/README.md).

### Desarrollo de la web sin Docker

```bash
cd web && npm install && npm run dev    # http://localhost:5173, con proxy de /api hacia localhost:8000
```

Nunca subir el archivo `.env` al repositorio.
