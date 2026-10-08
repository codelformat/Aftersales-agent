# Agent token 预算依赖上游主动返回 usage

- 状态：搁置
- 发现：ch05 全分支代码审查，2026-10-08

## 1. 现象

`AGENT_TOKEN_BUDGET` 按 `usage_metadata["total_tokens"]` 累计。base_url 不是 OpenAI 时，langchain-openai 默认不开 `stream_usage`，请求不带 `stream_options.include_usage`。DeepSeek 实测仍会返回 usage（含思考 token），换成其他 `CHAT_*` 上游时可能不返回。

## 2. 根因

没有返回 usage 时，代码退回 `count_tokens` 估算。估算不含思考 token，预算会少算。

## 3. 当时的决定和理由

搁置。当前上游 DeepSeek 返回 usage，生产行为正确。

## 4. 建议方案和改动范围

`app/llm.py:_build` 给聊天模型设 `stream_usage=True`。先用当前上游实测一次请求体和返回，确认不会 400。

## 5. 验收方法

换用不主动返回 usage 的上游（或抓请求体），确认请求带 `stream_options.include_usage`，`turn ... tokens=` 包含思考 token。
