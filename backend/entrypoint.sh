#!/usr/bin/env sh
set -e

# 生产部署：用迁移建表，禁止 AUTO_CREATE_TABLES
export AUTO_CREATE_TABLES="${AUTO_CREATE_TABLES:-false}"

alembic upgrade head
python -m app.scripts.ensure_default_admin

exec "$@"

