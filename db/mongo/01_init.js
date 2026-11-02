
<escape>// Clínica ambulatoria · Grupo 2
// Fase 3/4: estructura del catálogo MongoDB 7 para mongosh.
// Ejecutar solamente en el entorno final, no sobre catalogo_poc.
// Idempotente en colecciones e índices; NO elimina ni inserta documentos.
// Requiere replica set rs0 para que la API use transacciones catálogo + outbox.

const nombreBD = process.env.MONGO_DB || "catalogo";
const dbCatalogo = db.getSiblingDB(nombreBD);

const id = { bsonType: "string", minLength: 1 };
const nombre = { bsonType: "string", minLength: 3 };
const modalidades = {
  bsonType: "array",
  minItems: 1,
  uniqueItems: true,
  items: { enum: ["PRESENCIAL", "TELECONSULTA"] }
};

function asegurarColeccion(nombreColeccion, jsonSchema) {
  const validator = { $jsonSchema: jsonSchema };
  const existe = dbCatalogo.getCollectionNames().includes(nombreColeccion);
  if (!existe) {
    dbCatalogo.createCollection(nombreColeccion, {
      validator,
      validationLevel: "strict",
      validationAction: "error"
    });
    print("Colección creada: " + nombreColeccion);
  } else {
    // Actualizar el validador sin borrar los documentos existentes.
    dbCatalogo.runCommand({
      collMod: nombreColeccion,
      validator,
      validationLevel: "strict",
      validationAction: "error"
    });
    print("Validador comprobado: " + nombreColeccion);
  }
}

asegurarColeccion("especialidades", {
  bsonType: "object",
  required: ["_id", "nombre", "nombre_normalizado", "activo"],
  properties: {
    _id: id,
    nombre,
    nombre_normalizado: id,
    descripcion: { bsonType: "string" },
    activo: { bsonType: "bool" }
  }
});

asegurarColeccion("sedes", {
  bsonType: "object",
  required: ["_id", "nombre", "nombre_normalizado", "direccion_ficticia", "activo"],
  properties: {
    _id: id,
    nombre,
    nombre_normalizado: id,
    direccion_ficticia: id,
    ubicacion: {
      bsonType: "object",
      required: ["type", "coordinates"],
      properties: {
        type: { enum: ["Point"] },
        // GeoJSON: [longitud, latitud], coordenadas ficticias.
        coordinates: {
          bsonType: "array",
          minItems: 2,
          maxItems: 2,
          items: { bsonType: ["double", "int", "long", "decimal"] }
        }
      }
    },
    activo: { bsonType: "bool" }
  }
});

asegurarColeccion("profesionales", {
  bsonType: "object",
  required: ["_id", "nombre", "especialidad_ids", "sedes", "modalidades", "activo", "version"],
  properties: {
    _id: id,
    nombre,
    especialidad_ids: {
      bsonType: "array", minItems: 1, uniqueItems: true, items: id
    },
    sedes: {
      bsonType: "array", minItems: 1,
      items: {
        bsonType: "object",
        required: ["sede_id"],
        properties: {
          sede_id: id,
          horario_ref: { bsonType: "string" }
        }
      }
    },
    idiomas: { bsonType: "array", items: id },
    modalidades,
    certificaciones: { bsonType: "array", items: { bsonType: "string" } },
    activo: { bsonType: "bool" },
    version: { bsonType: ["int", "long"], minimum: 1 },
    actualizado_en: { bsonType: "date" }
  }
});

asegurarColeccion("servicios", {
  bsonType: "object",
  required: ["_id", "nombre", "especialidad_id", "duracion_minutos", "modalidades", "activo", "version"],
  properties: {
    _id: id,
    nombre,
    especialidad_id: id,
    duracion_minutos: { bsonType: ["int", "long"], minimum: 1 },
    modalidades,
    activo: { bsonType: "bool" },
    version: { bsonType: ["int", "long"], minimum: 1 },
    actualizado_en: { bsonType: "date" }
  }
});

asegurarColeccion("outbox", {
  bsonType: "object",
  required: ["tipo", "entidad_tipo", "entidad_id", "version", "creado_en", "procesado_en", "intentos"],
  properties: {
    tipo: { bsonType: "string", minLength: 1 },
    entidad_tipo: { enum: ["especialidad", "sede", "profesional", "servicio"] },
    entidad_id: id,
    version: { bsonType: ["int", "long"], minimum: 1 },
    creado_en: { bsonType: "date" },
    procesado_en: { bsonType: ["date", "null"] },
    intentos: { bsonType: ["int", "long"], minimum: 0 },
    ultimo_error: { bsonType: ["string", "null"] }
  }
});

// Índices útiles para validación, mantenimiento y sincronización.
// La normalización de nombres (trim, minúsculas, tildes) corresponde a la API.
dbCatalogo.especialidades.createIndex(
  { nombre_normalizado: 1 }, { unique: true, name: "uq_especialidad_nombre" }
);
dbCatalogo.sedes.createIndex(
  { nombre_normalizado: 1 }, { unique: true, name: "uq_sede_nombre" }
);
dbCatalogo.sedes.createIndex({ ubicacion: "2dsphere" }, { name: "ix_sede_geo", sparse: true });
dbCatalogo.profesionales.createIndex(
  { especialidad_ids: 1, activo: 1 }, { name: "ix_prof_especialidad_activo" }
);
dbCatalogo.profesionales.createIndex(
  { "sedes.sede_id": 1, activo: 1 }, { name: "ix_prof_sede_activo" }
);
dbCatalogo.servicios.createIndex(
  { especialidad_id: 1, activo: 1 }, { name: "ix_serv_especialidad_activo" }
);
dbCatalogo.outbox.createIndex(
  { procesado_en: 1, creado_en: 1 }, { name: "ix_outbox_pendientes" }
);
dbCatalogo.outbox.createIndex(
  { entidad_tipo: 1, entidad_id: 1, version: 1 },
  { unique: true, name: "uq_outbox_entidad_version" }
);

print("OK · Catálogo MongoDB inicializado en BD: " + nombreBD);
print("Colecciones: " + dbCatalogo.getCollectionNames().sort().join(", "));
print("ATENCIÓN: Este archivo no crea el worker ni ejecuta la sincronización.");</escape>
