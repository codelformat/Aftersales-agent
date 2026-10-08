# 同一步两次调用 offer_human_options 时按钮重复

状态：搁置（ch06 code review 发现）

1. **现象**：Agent 在同一步中两次调用 `offer_human_options`（例如都给 `["handoff"]`），前端出现两个「转人工」按钮。
2. **根因**：ch06 的 `agent_tools` 为了合并人工选项和退款按钮，改为按调用顺序拼接 actions。ch05 是"最后一次调用生效"。
3. **决定和理由**：暂不修。模型很少在同一步重复调用同一个控制工具；没有测试或实测出现过。
4. **建议方案**：拼接后按 `type`（ticket 再加 `ticket_type`）去重，保留第一次出现的项。改动范围：`app/graph/nodes/agent.py` 的 actions 合并段。
5. **验收**：单测给出两次 `offer_human_options(["handoff"])` 调用，断言 `actions == [{"type": "handoff"}]`。
