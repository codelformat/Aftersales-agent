export interface NarrationCue {
  anchor: { event: string; kind?: string; nth?: number; lane?: string };
  zh: string;
  en: string;
}
export interface SceneMetadata {
  id: string; titleZh: string; titleEn: string; proves: string; tags: string[];
  durationMs: number; layout: 'single' | 'split' | 'strategies'; narration: NarrationCue[];
}
function cue(event: string, zh: string, en: string, extra: Omit<NarrationCue['anchor'], 'event'> = {}): NarrationCue {
  return { anchor: { event, ...extra }, zh, en };
}
function trace(kind: string, zh: string, en: string, nth = 1): NarrationCue {
  return cue('trace', zh, en, { kind, nth, lane: 'customer' });
}
export const scenes: SceneMetadata[] = [
  {
    id: 'flywheel', titleZh: '知识飞轮：从兜底到答对', titleEn: 'Knowledge flywheel',
    proves: '人工核准的知识可用于下一次回答。', tags: ['置信度闸', '人工核准', '知识飞轮'], layout: 'split', durationMs: 8910,
    narration: [
      cue('chat', '客户问 L2 是否支持语音控制。现有知识缺少这一项。', 'The customer asks whether the L2 supports voice control. That detail is missing from the knowledge base.', { lane: 'customer' }),
      trace('gate', '证据置信度 0.0468，低于 0.39。闸拦下回答，避免猜测。', 'Evidence confidence is 0.0468, below 0.39. The gate blocks an answer that would require guessing.'),
      cue('poll_review', '新问题进入待审队列，附原话与召回快照。运营能检查缺口。', 'The new question reaches the review queue with the original wording and retrieved evidence, so an operator can inspect the gap.', { lane: 'ops', nth: 7 }),
      cue('approve', '运营核准答案。接口返回知识块 481，向量化数量为 1。', 'The operator approves an answer. The API returns knowledge chunk 481 and one vectorized chunk.', { lane: 'ops' }),
      trace('gate', '再次提问命中新知识。置信度升至 0.7984，闸通过。', 'The repeated question retrieves the new knowledge. Confidence rises to 0.7984 and the gate passes.', 2),
      cue('done', '回答引用新知识。缺口经人工核准进入检索，闭环到下一次回答。', 'The answer cites the new knowledge. Human review has turned a knowledge gap into evidence for the next answer.', { lane: 'customer', nth: 2 }),
    ],
  },
  {
    id: 'refund', titleZh: '退款：选单、恢复与申请', titleEn: 'Refund: select, resume and submit',
    proves: '选单恢复对话，退款申请由客户提交。', tags: ['interrupt / resume', '订单卡片', '退款单'], layout: 'single', durationMs: 20251,
    narration: [
      cue('chat', '客户想退耳机，但没有订单号。系统先确定具体订单。', 'The customer wants to return earphones but gives no order number. The system must identify the order first.'),
      cue('order_picker', '流程暂停并显示订单卡片，等客户选择。', 'The workflow pauses and shows order cards for the customer to choose from.'),
      cue('pick_order', '客户选择订单 205925。同一轮恢复，不重新猜订单。', 'The customer selects order 205925. The same turn resumes with a specific order.'),
      trace('retrieval', '扩写后的多个查询一起检索，再统一重排退货证据。', 'Expanded queries retrieve return-policy evidence, which is reranked together.'),
      cue('chat', '系统先说明退货条件。客户再明确要求提交退款单。', 'The system explains the return conditions first. The customer then explicitly asks for a refund form.', { nth: 2 }),
      cue('actions', '此时才出现退款按钮。准备表单还不等于提交申请。', 'The refund button appears now. Preparing a form does not submit the application.'),
      cue('refund', '退款接口返回申请号，状态为待审核。提交不代表已退款。', 'The refund API returns an application number with a pending-review status. Submission does not mean the refund is complete.'),
    ],
  },
  {
    id: 'multi-turn', titleZh: '多轮：指代与对话回顾', titleEn: 'References and conversation recall',
    proves: '补全商品指代，并从上下文回顾已有提问。', tags: ['指代消解', '上下文预算', '历史回顾'], layout: 'single', durationMs: 15131,
    narration: [
      trace('gate', '续航证据通过闸。回答会引用具体商品资料。', 'Battery-life evidence passes the gate. The answer can cite product-specific information.'),
      trace('resolve', '“它”补全为 X3 Pro，让检索继续围绕同一商品。', '“It” is resolved to the X3 Pro, keeping retrieval focused on the same product.', 2),
      trace('gate', '防水证据不足。记住商品不等于知道全部参数。', 'There is not enough evidence about water resistance. Remembering the product does not supply missing specifications.', 2),
      trace('resolve', '退货问题也补全商品名，再进入售后流程。', 'The return question also gets a product name before entering the aftersales workflow.', 3),
      trace('context', '已有对话占层 1 的 156 tokens。此录制未触发层 2 或摘要。', 'Earlier conversation uses 156 tokens in layer 1. This recording does not trigger layer 2 or a summary.', 4),
      trace('resolve', '识别为历史回顾，直接用会话上下文，不检索商品知识。', 'The question is recognized as conversation recall. It uses chat context instead of retrieving product knowledge.', 4),
      cue('done', '系统列出三个提问，也指出防水问题未得到可靠回答。', 'The system lists the three questions and notes that water resistance was not answered reliably.', { nth: 4 }),
    ],
  },
  {
    id: 'ticket', titleZh: '工单：确认后写入', titleEn: 'Confirm before creating a ticket',
    proves: '客户确认预览后，系统才执行写操作。', tags: ['写操作确认', '工单预览', '审计'], layout: 'single', durationMs: 6841,
    narration: [
      cue('done', '建单信息不足，系统先追问问题，尚未创建工单。', 'The request lacks details, so the system asks about the problem before creating a ticket.'),
      cue('chat', '客户补充左耳无声和维修需求，提供建单依据。', 'The customer describes a silent left earbud and asks for repair, supplying the ticket details.', { nth: 2 }),
      cue('ticket_preview', '写操作暂停在预览卡。客户可以检查类型和描述。', 'The write operation pauses at a preview card. The customer can check the type and description.'),
      cue('confirm_ticket', '客户确认后恢复流程，授权只用于本次工单。', 'Customer confirmation resumes the workflow and authorizes this ticket.'),
      trace('tool', 'create_ticket 执行成功，重试次数为 0，避免重复写入。', 'create_ticket succeeds with zero retries, avoiding duplicate writes.'),
      cue('snapshot', '审计快照留下工具状态和执行耗时，可核对这次写入。', 'The audit snapshot records the tool status and execution time, making the write traceable.', { lane: 'ops' }),
    ],
  },
  {
    id: 'mcp-timeout', titleZh: 'MCP：超时与降级', titleEn: 'MCP timeout and fallback',
    proves: '外部工具超时后，系统说明失败并提供人工入口。', tags: ['MCP', '超时重试', '降级'], layout: 'single', durationMs: 26411,
    narration: [
      cue('inject', '录制为物流服务注入 10 秒延迟，用来观察超时处理。', 'The recording injects a ten-second delay into the logistics service to exercise timeout handling.', { lane: 'ops' }),
      cue('tool_start', 'Agent 调用物流 MCP。长等待在回放中压缩，保留实际时长。', 'The agent calls the logistics MCP tool. Replay compresses the long wait but labels its real duration.'),
      trace('tool', '审计记录超时：共 3 次尝试、2 次重试。仍未取得物流结果。', 'The audit records a timeout after three attempts and two retries. No logistics result was obtained.'),
      cue('actions', '系统提供转人工入口，让客户有后续处理路径。', 'The system offers a human handoff so the customer has a next step.'),
      cue('done', '回答明确说明查询超时，不编造物流位置。', 'The answer explicitly reports the timeout instead of inventing a shipment location.'),
      cue('snapshot', '运营快照保留超时审计，便于定位外部服务故障。', 'The operations snapshot preserves the timeout audit for investigating the external service failure.', { lane: 'ops' }),
    ],
  },
  {
    id: 'boundaries', titleZh: '边界：超范围、投诉与闲聊', titleEn: 'Scope, complaints and greetings',
    proves: '不同意图走各自流程，并说明业务范围。', tags: ['业务边界', '投诉', '闲聊'], layout: 'single', durationMs: 6495,
    narration: [
      trace('intent', '宠物保险归为“其他”。这次录制由 Agent 说明业务边界。', 'Pet insurance is classified as “other.” In this recording, the agent explains the service boundary.'),
      cue('done', '回答说明缺少保险推荐依据，转回购物和售后范围。', 'The answer explains that it lacks a basis for insurance recommendations and returns to shopping and aftersales support.'),
      trace('intent', '投诉进入专用分支，先安抚客户。', 'The complaint enters a dedicated branch that first acknowledges the customer’s frustration.', 2),
      cue('actions', '人工和工单入口由客户选择，投诉话术本身不创建工单。', 'The customer chooses a handoff or ticket. The complaint reply itself does not create a ticket.'),
      trace('intent', '问候归为闲聊，使用固定话术，说明可以处理哪些问题。', 'The greeting is classified as small talk. A fixed reply explains what the service can handle.', 3),
    ],
  },
  {
    id: 'strategies', titleZh: '检索：四策略对比', titleEn: 'Four retrieval strategies',
    proves: '代表题排序与评估指标共同支持检索选型。', tags: ['dense / BM25', 'RRF / 重排', '真实评估'], layout: 'strategies', durationMs: 12000,
    narration: [
      cue('strategy_source', '读取评估快照。代表题排序和汇总指标来自两次独立运行。', 'The evaluation snapshot is loaded. Example rankings and aggregate metrics come from separate runs.', { lane: 'ops' }),
      cue('strategy_case', '“邮费”是口语问法。dense 的相关答案排第 1，BM25 排第 2。', '“Postage” is a short everyday query. The relevant answer ranks first for dense retrieval and second for BM25.', { lane: 'ops', nth: 1 }),
      cue('strategy_case', 'T5 电量显示题，BM25 首位命中。型号词能帮助关键词检索。', 'For the T5 battery-display question, BM25 ranks the relevant answer first. Model names can help keyword retrieval.', { lane: 'ops', nth: 2 }),
      cue('strategy_case', '质量退货期限题，四种策略都首位命中。单道题不足以选型。', 'All four strategies rank the relevant answer first for the defective-product return question. One example cannot decide the strategy.', { lane: 'ops', nth: 3 }),
      cue('strategy_metrics', '重排的 MRR 为 0.987，误拒率为 0.058。选型也要考虑拒答代价。', 'Reranking has an MRR of 0.987 and a false-refusal rate of 0.058. Strategy choice must also account for refusals.', { lane: 'ops' }),
    ],
  },
];
