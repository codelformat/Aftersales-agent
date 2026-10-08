# 新会话没有防御残留的 checkpoint

- 状态：搁置
- 发现：ch05 全分支代码审查，2026-10-08

## 1. 现象

不用 `scripts/reset_db.sh`、而是手动 `docker compose down -v` 重建 MySQL 后，会话 ID 从 1 重新开始。`data/checkpoints.sqlite` 中仍有旧的 `thread_id="1"`，新会话会读到旧会话的历史。

## 2. 根因

`thread_id` 直接取 `conversations.id`，两边的生命周期没有绑定。只有 `reset_db.sh` 会同时删除 sqlite 文件。

## 3. 当时的决定和理由

搁置。CLAUDE.md 已规定重建必须用 `reset_db.sh`，并删除 sqlite 文件。

## 4. 建议方案和改动范围

`prepare_chat_turn` 在 `session_id is None` 创建会话后，读取 `aget_state`；值不为空时记 ERROR 并拒绝（或改用带前缀的 thread_id，例如 `"<创建时间戳>-<id>"`）。改动范围：`app/api/chat.py`、`app/graph/builder.thread_config`。

## 5. 验收方法

测试中先对 thread "1" 写入 State，再新建会话 1，断言请求被拒绝或读不到旧历史。
