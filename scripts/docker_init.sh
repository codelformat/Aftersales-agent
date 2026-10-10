#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
uv run python - <<'PY'
import asyncio
import re
import subprocess
import sys
from urllib.parse import urlsplit

import httpx
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.retry import retry_async


async def wait_for_services():
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url, poolclass=NullPool, connect_args={"connect_timeout": 5}
    )
    uri = urlsplit(settings.milvus_uri)
    host = uri.hostname
    if not host:
        raise ValueError("MILVUS_URI must contain a hostname")
    if ":" in host:
        host = f"[{host}]"
    health_url = uri._replace(netloc=f"{host}:9091", path="/healthz", query="", fragment="").geturl()

    async def mysql_ready():
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            async def milvus_ready():
                response = await client.get(health_url)
                response.raise_for_status()

            for name, probe in (("MySQL", mysql_ready), ("Milvus", milvus_ready)):
                print(f"Waiting for {name}", flush=True)
                await retry_async(
                    probe, attempts=30, base_delay=1, max_delay=10,
                    retry_on=(SQLAlchemyError, httpx.HTTPError),
                    on_retry=lambda attempt, exc: print(f"Not ready; retry {attempt}", flush=True),
                )
    finally:
        await engine.dispose()


asyncio.run(wait_for_services())
command = [sys.executable, "scripts/build_kb.py"]
status = subprocess.run(command + ["--status"], check=True, capture_output=True, text=True)
print(status.stdout, end="", flush=True)
if status.stderr:
    print(status.stderr, end="", file=sys.stderr)
counts = re.search(r"MySQL 合计 (\d+)（pending (\d+)，done (\d+)）；Milvus 实体数 (\d+)", status.stdout)
if counts is None:
    raise RuntimeError("Unrecognized build_kb.py --status output")
total, pending, done, milvus = map(int, counts.groups())
if total > 0 and pending == 0 and done == total and milvus == total:
    print("Knowledge base is complete; skipping build", flush=True)
else:
    # Ingestion skips existing documents; vectorization upserts by MySQL ID.
    subprocess.run(command, check=True)
    subprocess.run(command + ["--check"], check=True)
PY
