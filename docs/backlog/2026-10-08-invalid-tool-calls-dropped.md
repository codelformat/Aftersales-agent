# Agent 丢弃格式错误的工具调用

- 状态：搁置
- 发现：ch05 全分支代码审查，2026-10-08

## 1. 现象

模型返回的工具调用参数不是合法 JSON 时，LangChain 把它放进 `invalid_tool_calls`，不放进 `tool_calls`。`agent_model`（`app/graph/nodes/agent.py`）只读 `tool_calls`：
- 正文为空时，本轮判为"模型返回空回复"，发 `error`。
- 正文不为空时（例如"我帮您查一下"），这句话被当作最终回复。

## 2. 根因

`AIMessage(content=text, tool_calls=tool_calls)` 没有带上 `invalid_tool_calls`，执行器看不到这些调用，无法返回 `invalid_arguments` 让模型改正。ch02 的单轮服务也是这样处理。

## 3. 当时的决定和理由

搁置。DeepSeek 实测没有出现过参数 JSON 格式错误；本章重点是图骨架。

## 4. 建议方案和改动范围

在 `agent_model` 中把 `gathered.invalid_tool_calls` 当作工具调用流向 `agent_tools`；`agent_tools` 为它们生成 `failure_outcome(id, name, "invalid_arguments")`。改动范围：`app/graph/nodes/agent.py`、`app/graph/routing.py:after_agent`。

## 5. 验收方法

用 `ScriptedChatModel` 生成参数为非法 JSON 的 tool_call chunk，断言下一次模型调用能看到 `invalid_arguments` 的工具消息，本轮最终正常回答。
