# 订单选择器待处理时点工单或退款按钮会清掉待选状态

状态：搁置（ch06 code review 发现）

1. **现象**：会话中有待处理的订单选择器（图停在 `ensure_order`）时，用户点了之前某轮的「建工单」或「提交退款单」，之后再点订单卡片返回 409 `no_pending_selection`。
2. **根因**：`POST /tickets` 和 `POST /refunds` 用 `aupdate_state(..., as_node="finalize")` 把提示写入 State，这会让图从 `finalize` 之后继续，待处理的 interrupt 被丢弃。
3. **决定和理由**：暂不修。不会报错，用户重新提问即可；需要同时点旧按钮和新卡片，场景少。
4. **建议方案**：两个接口写 State 前读 `aget_state().interrupts`；有待处理的 `order_picker` 时只写 `messages` 表，或在写入后保留 interrupt（需先用 Context7 和实测确认 LangGraph 的可行做法）。改动范围：`app/api/tickets.py`、`app/api/refunds.py`。
5. **验收**：API 测试：弹出选择器 → 调 `/refunds` → 再调 `/chat/resume` 返回 200 且子流程走完。
