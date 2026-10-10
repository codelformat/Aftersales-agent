# 前端开发

需要 Node.js 22.22.2、24.15.0 或 26 及以上版本。
在仓库根目录执行以下命令。

```bash
npm --prefix web ci
npm --prefix web run dev
```

开发服务将 `/api`、`/chat`、`/tickets`、`/refunds` 代理到 `http://127.0.0.1:8000`。

```bash
npm --prefix web run typecheck
npm --prefix web test
npm --prefix web run build
npm --prefix web run build:replay
```

`build` 使用 live 数据源，清空并写入 `app/web/dist/`。
构建后启动后端，访问 `http://127.0.0.1:8000/`。
`build:replay` 使用 replay 数据源，写入 `web/dist-replay/`。
回放版资源前缀为 `/Aftersales-agent/`。
构建模式直接选择 `VITE_DATA_SOURCE`，无需修改 `.env`。

页面使用 hash 路由。目前各路由显示占位页。
视角保存在 `localStorage` 的 `view_mode` 键。
`eng` 表示工程视角，`customer` 表示客户视角。
