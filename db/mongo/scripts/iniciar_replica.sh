#!/usr/bin/env bash
# Inicia el replica set rs0 (una sola vez) y aplica validadores/índices del catálogo.
# Es idempotente: si el replica set ya existe solo vuelve a comprobar validadores e índices.
set -euo pipefail

HOST="mongo:27017"

echo "[mongo-init] Comprobando replica set..."
mongosh --host "$HOST" --quiet --eval '
try {
  rs.status();
  print("Replica set ya iniciado");
} catch (e) {
  rs.initiate({ _id: "rs0", members: [{ _id: 0, host: "mongo:27017" }] });
  print("Replica set rs0 iniciado");
}'

echo "[mongo-init] Esperando nodo primario..."
for i in $(seq 1 60); do
  if mongosh --host "$HOST" --quiet --eval 'db.hello().isWritablePrimary' | grep -q true; then
    break
  fi
  sleep 1
done

echo "[mongo-init] Aplicando esquema del catálogo (db/mongo/01_init.js)..."
mongosh "mongodb://$HOST/?replicaSet=rs0" --quiet /init/01_init.js
echo "[mongo-init] Listo."
