# evals/hard_set/prompts.py
"""困难评估集的出题与标注 Prompt。"""

import hashlib

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field


class GeneratedQuery(BaseModel):
    query: str = Field(description="顾客问题")
    term: str | None = Field(default=None, description="H3 用：问题中决定答案的术语")


class LabelAnswer(BaseModel):
    answers: list[int] = Field(description="能完整回答问题的候选编号")


class PairAnswer(BaseModel):
    answerable: bool = Field(description="该段落能否完整回答问题")


_COMMON = """你在为电商售后客服的检索系统编写测试问题。只输出 JSON 对象，不输出其他文字。
目标段落：
标题：{target_title}
正文：{target_text}

不要和下列已有问题问同一件事：
{avoid}"""

_H1 = _COMMON + """

同系列其他型号的同名段落：
{distractors}

要求：
1. 问题必须写出目标段落的型号，不写其他型号。
2. 问的事实要在目标段落中有明确答案，并且在其他型号的段落中数值或说法不同。
3. 用顾客口吻，一句话，不超过 40 字。

示例 1：目标「X3 Pro 续航」→ {{"query": "X3 Pro 开着降噪大概能听几个小时？"}}
示例 2：目标「S10 Max 保修」→ {{"query": "S10 Max 的主机保几年？"}}

输出格式：{{"query": "..."}}"""

_H2 = _COMMON + """

要求：
1. 用顾客口吻描述遇到的现象或场景，让人读完就知道要找这个段落。
2. 不用标题中的词，尽量不用正文中的词，换成日常说法。
3. 段落属于某个型号时，可以写出型号。
4. 一到两句，不超过 60 字。

示例 1：目标「X3 常见故障」（只有一边有声音）→ {{"query": "X3 戴着只有左耳出声，右边一点动静没有，咋回事"}}
示例 2：目标「订单取消」→ {{"query": "刚付完款就后悔了，东西还没寄出来，这单还能撤回吗"}}

输出格式：{{"query": "..."}}"""

_H3 = _COMMON + """

要求：
1. 从目标段落中选 1 个能决定答案的术语（2–8 字，原文照抄，例如“价格保护”“上门取件”）。
2. 写一段 60–150 字的顾客留言：前面是与问题无关的闲聊（购物经历、家人、物流等），术语只在最后提问时出现 1 次。
3. 闲聊不涉及目标段落中的事实，不出现其他售后术语。

示例 1：{{"term": "价格保护", "query": "我上周给家里老人买了个保温杯，还给孩子挑了双运动鞋，快递第二天就到了，包装也挺严实，老人用着说挺好。今天刷到同款便宜了二十多块，想问问价格保护怎么申请？"}}
示例 2：{{"term": "上门取件", "query": "前阵子搬家，东西乱七八糟堆了一屋，最近才收拾完，发现扫地机有点毛病已经申请了维修。我腿脚不太方便，不想自己跑快递点，你们能上门取件吗？"}}

输出格式：{{"term": "...", "query": "..."}}"""

_H4 = _COMMON + """

看起来相关、但不能给出正确答案的通用段落：
{distractors}

要求：
1. 问题必须写出目标段落的型号，不写其他型号。
2. 用通用说法提问（例如“保修多久”“能退吗”“能用多久”），不写段落标题或章节名。
3. 答案只能从目标段落得到；上面的通用段落给不出这个答案，或者给出的答案不对。
4. 用顾客口吻，一句话，不超过 40 字。

示例 1：目标「X3 Pro 续航」，通用段落「耳机充满电能用多久？」（写的是 X3 的数值）→ {{"query": "X3 Pro 充满一次能用多久？"}}
示例 2：目标「X3 保修」，通用段落「保修期」（只说以详情页为准）→ {{"query": "X3 耳机进水了还能免费修吗？"}}

输出格式：{{"query": "..."}}"""

GEN_PROMPTS = {
    bucket: ChatPromptTemplate.from_messages([("system", text), ("human", "请输出 JSON。")])
    for bucket, text in (("H1_sibling", _H1), ("H2_colloquial", _H2), ("H3_buried", _H3), ("H4_specific", _H4))
}

_LABEL = """你在为检索测试集做标注。下面是知识库候选段落，每段以 [编号] 开头。
读完顾客问题后，找出所有能完整回答这个问题的段落。只回答了一部分、讲的是其他型号、或只说"以详情页为准"的段落不算。
{model_info}
通用政策（退货政策、售后手册、常见问答）适用于所有商品；通用段落给出的答案对问题中的型号同样成立时，算能回答。
知识库约定：商品FAQ 中未注明型号的耳机、电动牙刷、台灯、保温杯参数分别对应 X3、T3、L1、C5；问其他型号时，这些 FAQ 段落不算能回答。按品类列出的规则表，对该品类的所有型号成立。
只输出 JSON 对象：{{"answers": [编号, ...]}}；没有能回答的段落时输出 {{"answers": []}}。

候选段落：
{candidates}"""

LABEL_PROMPT = ChatPromptTemplate.from_messages([("system", _LABEL), ("human", "顾客问题：{query}")])


_RECALL = """你在为检索测试集做通用段落召回。下面每段以 [编号] 开头。
任务是从下面的通用段落（退货政策、售后手册、常见问答、商品FAQ）中找出所有可能完整回答顾客问题的段落。
宁可多选，之后会逐段复核。
{model_info}
知识库约定：商品FAQ 中未注明型号的耳机、电动牙刷、台灯、保温杯参数分别对应 X3、T3、L1、C5；问其他型号时，这些 FAQ 段落不算能回答。按品类列出的规则表，对该品类的所有型号成立。
只输出 JSON 对象 {{"answers": [编号, ...]}}，没有时输出 {{"answers": []}}。

候选段落：
{candidates}"""

RECALL_PROMPT = ChatPromptTemplate.from_messages([("system", _RECALL), ("human", "顾客问题：{query}")])


_PAIR = """你在为检索测试集做成对复核。判断下面这一个知识库段落能否完整回答顾客问题。
{model_info}
段落给出的事实对问题中的型号成立，并且覆盖问题要点时，算能回答。
讲的是其他型号、只回答了一部分、或只说"以详情页为准"的段落不算。
段落主题与问题不同的不算，例如问保修期限时，讲退换货期限、维修时长或只说以详情页为准的段落都不算；手册或政策的简介段不算。段落必须直接给出问题要的具体答案。
通用政策（退货政策、售后手册、常见问答）适用于所有商品；通用段落给出的答案对问题中的型号同样成立时，算能回答。
知识库约定：商品FAQ 中未注明型号的耳机、电动牙刷、台灯、保温杯参数分别对应 X3、T3、L1、C5；问其他型号时，这些 FAQ 段落不算能回答。按品类列出的规则表，对该品类的所有型号成立。
只输出 JSON 对象 {{"answerable": true}} 或 {{"answerable": false}}，不输出其他文字。

段落标题：{chunk_title}
段落正文：{chunk_text}"""

PAIR_PROMPT = ChatPromptTemplate.from_messages([("system", _PAIR), ("human", "顾客问题：{query}")])


def prompt_sha256() -> dict[str, str]:
    texts = {"H1_sibling": _H1, "H2_colloquial": _H2, "H3_buried": _H3, "H4_specific": _H4,
             "label": _LABEL, "recall": _RECALL, "pair": _PAIR}
    return {k: hashlib.sha256(v.encode("utf-8")).hexdigest() for k, v in texts.items()}
