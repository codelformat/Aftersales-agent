#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
docker compose down -v
docker compose up -d --wait
docker exec aftersales-mysql mysql -uaftersales -paftersales -e "SELECT COUNT(*) FROM aftersales.faq;"
