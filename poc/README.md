# Pruebas pequeñas · Fase 2

Una prueba por gestor para respaldar la [matriz de selección](../docs/fase2-investigacion.md). **No** son parte de la aplicación final.

## Requisitos

- Docker Desktop (o Docker Engine con Compose v2)
- Python 3.11 o superior

## Ejecución

Desde la raíz del repositorio:

```bash
# 1. Levantar los cuatro gestores (puertos solo en 127.0.0.1)
docker compose -f poc/docker-compose.poc.yml up -d

# 2. Esperar a que MongoDB inicialice el replica set (estado "healthy", unos 10–20 s)
docker compose -f poc/docker-compose.poc.yml ps

# 3. Instalar dependencias en un entorno virtual
python -m venv .venv
# Windows: .venv\Scripts\activate    ·    Linux/macOS: source .venv/bin/activate
pip install -r poc/requirements.txt

# 4. Ejecutar cada prueba
python poc/01_postgres_concurrencia.py
python poc/02_mongo_catalogo_outbox.py
python poc/03_typesense_busqueda.py
python poc/04_redis_ttl.py

# 5. Apagar (con -v se borran también los volúmenes de prueba)
docker compose -f poc/docker-compose.poc.yml down -v
```

Cada script termina con una línea `OK · ...`. Si alguna comprobación falla, se detiene con `AssertionError`.

## Qué guardar como evidencia

Captura de la salida de cada script en `docs/evidencias/fase2/` y completar la tabla de resultados de la sección 8 de [fase2-investigacion.md](../docs/fase2-investigacion.md).

## Salida esperada (resumen)

```
A. 20 reservas simultáneas -> 1 con 201, 19 con 409
B. Reintento con la misma clave -> 200, misma cita: True
C. Cupo solapado del mismo profesional -> rechazado por la base (ExclusionViolation)
D. Tras cancelar: nueva reserva -> 201; historial de la cita cancelada: ['PENDIENTE', 'CANCELADA']
```
