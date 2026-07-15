#!/usr/bin/env sh
# Arranque del backend en producción (Render, etc.):
# 1) aplica las migraciones (crea/actualiza las tablas)
# 2) siembra permisos, roles y el usuario admin (idempotente)
# 3) levanta el servidor en el puerto que asigne la plataforma ($PORT) o 8000
set -e

echo "==> Aplicando migraciones (alembic upgrade head)"
alembic upgrade head

echo "==> Sembrando datos base (roles, permisos, admin)"
python -m app.seeds.seed

echo "==> Iniciando servidor en el puerto ${PORT:-8000}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
