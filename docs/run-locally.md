# 本地运行

在仓库根目录执行命令。先安装 Docker Compose。

完整系统含运营台写操作，未配置鉴权。
端口 8000 只绑定 `127.0.0.1`。

1. 复制配置，并填写模型参数和密钥。

   ```bash
   cp .env.example .env
   ```

   填写 `CHAT_BASE_URL`、`CHAT_MODEL`、`CHAT_API_KEY`。
   使用 DeepSeek 时，填写 `CHAT_THINKING`。
   填写 `EMBED_API_KEY`、`RERANK_API_KEY`。

2. 启动完整系统。

   ```bash
   docker compose --profile full up -d --build --wait
   ```

3. 用浏览器打开 <http://127.0.0.1:8000/>。

## 开发模式

先按第 1 步填写 `.env`。
安装 uv 和 Node.js。
Node.js 使用 22.22.2、24.15.0 或 26 及以上版本。

安装依赖，启动 MySQL 和 Milvus，再构建知识库。

```bash
uv sync
docker compose up -d --wait
uv run python scripts/build_kb.py
```

启动后端。

```bash
uv run uvicorn app.main:app --reload --port 8000
```

在另一个终端启动前端。

```bash
npm --prefix web ci
npm --prefix web run dev
```

打开前端命令输出的本地地址。
前端将 API 请求代理到本机端口 8000。

## 可选：Langfuse

启动本项目的 Langfuse。

```bash
docker compose -f docker-compose.langfuse.yml up -d --wait
```

打开 <http://127.0.0.1:3100/>，创建项目和密钥。
在 `.env` 填写 `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`。
开发模式将 `LANGFUSE_BASE_URL` 设为 `http://127.0.0.1:3100`。
full 模式设为 `http://host.docker.internal:3100`。
重启后端或 full 模式的 app，让配置生效。
