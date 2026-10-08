# 搁置问题清单

本目录记录开发中发现、但当时决定暂不解决的问题。每个问题一个文件，文件名为 `YYYY-MM-DD-<主题>.md`。

每个文件写清 5 项：
1. 现象（带可复现的例子）；
2. 根因；
3. 当时的决定和理由；
4. 建议方案和改动范围；
5. 验收方法。

解决后，在文件开头的状态行写明"已解决"、解决的章节和提交号，不删除文件。

| 文件 | 问题 | 发现章节 | 状态 |
|---|---|---|---|
| [2026-10-07-self-check-partial-answer.md](2026-10-07-self-check-partial-answer.md) | 自评"全有或全无"，部分可答的问题整条被拒答 | ch04 | 搁置 |
| [2026-10-08-invalid-tool-calls-dropped.md](2026-10-08-invalid-tool-calls-dropped.md) | Agent 丢弃格式错误的工具调用 | ch05 | 搁置 |
| [2026-10-08-token-budget-usage-source.md](2026-10-08-token-budget-usage-source.md) | token 预算依赖上游主动返回 usage | ch05 | 搁置 |
| [2026-10-08-stale-checkpoint-guard.md](2026-10-08-stale-checkpoint-guard.md) | 新会话没有防御残留的 checkpoint | ch05 | 搁置 |
| [2026-10-08-actions-after-error.md](2026-10-08-actions-after-error.md) | 本轮出错后人工选项按钮仍可点击 | ch05 | 搁置 |
