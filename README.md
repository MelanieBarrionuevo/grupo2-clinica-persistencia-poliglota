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
│   ├── fase1-comprension.md
│   ├── fase2-investigacion.md
│   ├── diagramas/
│   ├── evidencias/ Capturas y video de la defensa
│   └── enunciado/
├── .env.example
└── docker-compose.yml   (Fase 4)
```

## Avance por fases

| Fase | Producto de control | Estado |
| --- | --- | --- |
| 1. Comprensión | Mapa de dominio y flujos | Completada |
| 2. Investigación | Matriz argumentada de selección de gestores | Completada |
| 3. Diseño | Modelo de datos, contratos de API, diagramas | Pendiente |
| 4. Implementación | Repositorio ejecutable con Docker Compose | Pendiente |
| 5. Validación | Pruebas y evidencias | Pendiente |
| 6. Defensa | Exposición y memoria técnica | Pendiente |

## Ejecución

> Se completará en la Fase 4.

```bash
cp .env.example .env      # ajustar valores ficticios
docker compose up --build -d
```

Nunca subir el archivo `.env` al repositorio.
