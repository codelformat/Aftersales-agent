import json
import subprocess
import sys

import pytest
from langchain_core.runnables import RunnableLambda

from app.knowledge.query import load_lexicon
from evals.hard_set import labels, pipeline, prompts
from evals.hard_set.corpus import Chunk
from evals.hard_set.filters import models_in
from evals.hard_set.prompts import GeneratedQuery, LabelAnswer
from evals.hard_set.seeds import Seed

pytestmark = pytest.mark.anyio

LEX = load_lexicon()
FORBIDDEN = ("app.knowledge.retrieval", "app.knowledge.milvus", "app.knowledge.rerank", "app.knowledge.embeddings")


def mk(key, model):
    return Chunk(key, key, key.split(" > ")[-1], f"{model} 正文 {key}", "蓝牙耳机", "商品手册", model, "续航")


CHUNKS = [mk("商品手册 > 蓝牙耳机 > X3 续航", "X3"), mk("商品手册 > 蓝牙耳机 > X3 Pro 续航", "X3 Pro"),
          mk("商品手册 > 蓝牙耳机 > X5 续航", "X5")]


def pools(n_targets=3):
    seeds = [Seed("H1_sibling", c.key, 1, ()) for c in CHUNKS[:n_targets]]
    return {"H1_sibling": seeds, "H2_colloquial": [], "H3_buried": [], "H4_specific": []}


def gen_for(script):
    """script: target_title -> 依次返回的问题列表；值为 Exception 时抛出，None 时 parsed 为 None。"""
    calls = {}

    def run(inputs):
        title = inputs["target_title"]
        i = calls.get(title, 0)
        calls[title] = i + 1
        item = script[title][min(i, len(script[title]) - 1)]
        if isinstance(item, Exception):
            raise item
        return {"parsed": None if item is None else GeneratedQuery(query=item), "raw": "r"}
    return RunnableLambda(run), calls


def labeler_target_only():
    def run(inputs):
        lines = [l for l in inputs["candidates"].splitlines() if l.startswith("[")]
        model = models_in(inputs["query"], LEX)[0]
        for line in lines:
            if line.endswith(f"> {model} 续航"):
                return {"parsed": LabelAnswer(answers=[int(line[1:line.index("]")])]), "raw": "r"}
        return {"parsed": LabelAnswer(answers=[99]), "raw": "r"}
    return RunnableLambda(run)


def recall_labeler_empty():
    return RunnableLambda(lambda _: {"parsed": LabelAnswer(answers=[]), "raw": "r"})


def pair_checker_false():
    return RunnableLambda(lambda _: {"parsed": prompts.PairAnswer(answerable=False), "raw": "r"})


async def synth(script, per_bucket, n_targets=3, labeler=None):
    gen, calls = gen_for(script)
    gens = {b: gen for b in ("H1_sibling", "H2_colloquial", "H3_buried", "H4_specific")}
    samples, stats = await pipeline.synthesize(
        pools(n_targets), CHUNKS, LEX, generators=gens, labeler=labeler or labeler_target_only(),
        recall_labeler=recall_labeler_empty(), pair_checker=pair_checker_false(),
        per_bucket={"H1_sibling": per_bucket, "H2_colloquial": 0, "H3_buried": 0, "H4_specific": 0},
        concurrency=2)
    return samples, stats, calls


async def test_retries_then_accepts_and_assigns_ids():
    script = {"X3 续航": ["X3 续航能用多久"], "X3 Pro 续航": ["X5 和 X3 Pro 比", RuntimeError("boom"), "X3 Pro 能用多久"],
              "X5 续航": ["X5 能用多久"]}
    samples, stats, calls = await synth(script, 2)
    assert [(s.id, s.query) for s in samples] == [("H1_01", "X3 续航能用多久"), ("H1_02", "X3 Pro 能用多久")]
    assert samples[1].keys == ("商品手册 > 蓝牙耳机 > X3 Pro 续航",)
    assert calls["X3 Pro 续航"] == 3 and "X5 续航" not in calls
    st = stats["H1_sibling"]
    assert st.rejected == {"含其他型号": 1} and st.errors == 1 and st.attempts == 4


async def test_labeler_receives_model_info():
    received = []

    def label(inputs):
        received.append(inputs)
        return {"parsed": LabelAnswer(answers=[1]), "raw": "r"}

    samples, _, _ = await synth({"X3 续航": ["X3 能用多久"]}, 1, n_targets=1,
                                labeler=RunnableLambda(label))
    assert len(samples) == 1
    assert len(received) == 1
    assert "model_info" in received[0]
    assert received[0]["model_info"] == "问题中的型号：X3（品类：蓝牙耳机）"


async def test_replaces_failed_seed_in_order():
    script = {"X3 续航": [None], "X3 Pro 续航": ["X3 Pro 能用多久"], "X5 续航": ["X5 能用多久"]}
    samples, stats, calls = await synth(script, 2)
    assert [s.target_key.split(" > ")[-1] for s in samples] == ["X3 Pro 续航", "X5 续航"]
    assert calls["X3 续航"] == 3 and stats["H1_sibling"].replaced == ["商品手册 > 蓝牙耳机 > X3 续航"]
    assert stats["H1_sibling"].rejected == {"解析失败": 3}


async def test_label_out_of_range_is_rejection():
    bad = RunnableLambda(lambda _: {"parsed": LabelAnswer(answers=[0]), "raw": "r"})
    script = {"X3 续航": ["X3 能用多久"], "X3 Pro 续航": ["X3 Pro 能用多久"], "X5 续航": ["X5 能用多久"]}
    samples, stats, _ = await synth(script, 1, labeler=bad)
    assert samples == [] and stats["H1_sibling"].rejected == {"标注编号无效": 9}


async def test_duplicate_query_in_same_batch_is_dropped():
    script = {"X3 续航": ["X3 能用多久"], "X3 Pro 续航": ["X3 Pro 能用多久"], "X5 续航": ["X5 能用多久"]}
    gen, _ = gen_for(script)
    seeds = [Seed("H1_sibling", CHUNKS[0].key, 1, ()), Seed("H1_sibling", CHUNKS[0].key, 2, ()),
             Seed("H1_sibling", CHUNKS[1].key, 1, ())]
    samples, stats = await pipeline.synthesize(
        {"H1_sibling": seeds, "H2_colloquial": [], "H3_buried": [], "H4_specific": []}, CHUNKS, LEX,
        generators={"H1_sibling": gen}, labeler=labeler_target_only(),
        recall_labeler=recall_labeler_empty(), pair_checker=pair_checker_false(),
        per_bucket={"H1_sibling": 2, "H2_colloquial": 0, "H3_buried": 0, "H4_specific": 0})
    assert [s.query for s in samples] == ["X3 能用多久", "X3 Pro 能用多久"]
    assert stats["H1_sibling"].rejected.get("问题重复", 0) >= 1


async def test_pair_review_rejects_h1_when_sibling_can_answer():
    gen, _ = gen_for({"X3 续航": ["X3 能用多久"]})
    checker = RunnableLambda(lambda inputs: {
        "parsed": prompts.PairAnswer(answerable=inputs["chunk_title"] == CHUNKS[1].key), "raw": "r"})
    samples, stats = await pipeline.synthesize(
        pools(1), CHUNKS, LEX, generators={"H1_sibling": gen}, labeler=labeler_target_only(),
        recall_labeler=recall_labeler_empty(), pair_checker=checker,
        per_bucket={"H1_sibling": 1, "H2_colloquial": 0, "H3_buried": 0, "H4_specific": 0})
    assert samples == []
    assert stats["H1_sibling"].rejected == {"同系列其他型号也能回答": 3}


async def test_pair_review_rejects_h4_when_general_policy_can_answer():
    target = Chunk("商品手册 > 蓝牙耳机 > X3 保修", "商品手册 > 蓝牙耳机 > X3 保修",
                   "X3 保修", "保修一年", "蓝牙耳机", "商品手册", "X3", "保修")
    general = Chunk("售后手册 > 维修 > 保修期", "售后手册 > 维修 > 保修期",
                    "保修期", "所有商品保修一年", "通用", "售后手册", None, None)
    gen, _ = gen_for({"X3 保修": ["X3 保多久？"]})
    checker = RunnableLambda(lambda inputs: {
        "parsed": prompts.PairAnswer(answerable=inputs["chunk_title"] == general.key), "raw": "r"})
    samples, stats = await pipeline.synthesize(
        {"H1_sibling": [], "H2_colloquial": [], "H3_buried": [],
         "H4_specific": [Seed("H4_specific", target.key, 1, (general.key,))]},
        [target, general], LEX, generators={"H4_specific": gen},
        labeler=RunnableLambda(lambda _: {"parsed": LabelAnswer(answers=[1]), "raw": "r"}),
        recall_labeler=recall_labeler_empty(), pair_checker=checker,
        per_bucket={"H1_sibling": 0, "H2_colloquial": 0, "H3_buried": 0, "H4_specific": 1})
    assert samples == []
    assert stats["H4_specific"].rejected == {"通用段落也能回答": 3}


async def test_pair_review_adds_answerable_product_faq_to_h1():
    faq = Chunk("商品FAQ > 蓝牙耳机 > 耳机充满电能用多久？", "商品FAQ > 蓝牙耳机 > 耳机充满电能用多久？",
                "耳机充满电能用多久？", "约 6 小时", "蓝牙耳机", "商品FAQ", None, None)
    gen, _ = gen_for({"X3 续航": ["X3 能用多久"]})
    checked = []

    def check(inputs):
        if inputs["chunk_title"] == faq.key:
            checked.append(inputs)
            assert inputs == {"chunk_title": faq.key, "chunk_text": faq.text, "query": "X3 能用多久",
                              "model_info": "问题中的型号：X3（品类：蓝牙耳机）"}
        return {"parsed": prompts.PairAnswer(answerable=inputs["chunk_title"] == faq.key), "raw": "r"}

    samples, stats = await pipeline.synthesize(
        pools(1), CHUNKS + [faq], LEX, generators={"H1_sibling": gen}, labeler=labeler_target_only(),
        recall_labeler=recall_labeler_empty(), pair_checker=RunnableLambda(check),
        per_bucket={"H1_sibling": 1, "H2_colloquial": 0, "H3_buried": 0, "H4_specific": 0})
    assert len(samples) == 1
    assert samples[0].keys == (CHUNKS[0].key, faq.key)
    assert len(checked) == 2 and checked[0] == checked[1]
    assert stats["H1_sibling"].rejected == {}


@pytest.mark.parametrize("fail_on", [1, 2])
async def test_pair_review_parse_failure_retries_and_rejects_attempt(fail_on):
    gen, calls = gen_for({"X3 续航": ["X3 能用多久"]})
    checked = []

    def check(inputs):
        checked.append(inputs["chunk_title"])
        parsed = None if len(checked) % fail_on == 0 else prompts.PairAnswer(answerable=True)
        return {"parsed": parsed, "raw": "r"}

    samples, stats = await pipeline.synthesize(
        pools(1), CHUNKS, LEX, generators={"H1_sibling": gen}, labeler=labeler_target_only(),
        recall_labeler=recall_labeler_empty(), pair_checker=RunnableLambda(check),
        per_bucket={"H1_sibling": 1, "H2_colloquial": 0, "H3_buried": 0, "H4_specific": 0})
    assert samples == []
    assert stats["H1_sibling"].rejected == {"复核解析失败": 3}
    assert calls == {"X3 续航": 3}
    assert checked == [CHUNKS[1].key] * (3 * fail_on)
    assert stats["H1_sibling"].errors == 0


def exchange_chunks():
    target = Chunk("商品手册 > 蓝牙耳机 > X3 退换规则", "商品手册 > 蓝牙耳机 > X3 退换规则",
                   "X3 退换规则", "换货只能换同款", "蓝牙耳机", "商品手册", "X3", "退换规则")
    general = Chunk("售后手册 > 换货 > 申请换货", "售后手册 > 换货 > 申请换货",
                    "申请换货", "换货只能换同款", "通用", "售后手册", None, None)
    assert labels.same_topic_keys(target, [target, general]) == []
    return target, general


async def synth_exchange(recall_labeler, pair_checker):
    target, general = exchange_chunks()
    gen, calls = gen_for({target.title: ["X3 换货只能换同款吗？"]})
    samples, stats = await pipeline.synthesize(
        {"H1_sibling": [], "H2_colloquial": [], "H3_buried": [],
         "H4_specific": [Seed("H4_specific", target.key, 1, ())]},
        [target, general], LEX, generators={"H4_specific": gen},
        labeler=RunnableLambda(lambda _: {"parsed": LabelAnswer(answers=[1]), "raw": "r"}),
        recall_labeler=recall_labeler, pair_checker=pair_checker,
        per_bucket={"H1_sibling": 0, "H2_colloquial": 0, "H3_buried": 0, "H4_specific": 1})
    return samples, stats["H4_specific"], calls


@pytest.mark.parametrize("answers, rejected, pair_calls, attempts", [
    pytest.param((True, False), {}, 2, 1, id="true-then-false"),
    pytest.param((False,), {}, 1, 1, id="false-short-circuits"),
    pytest.param((True, True), {"通用段落也能回答": 3}, 6, 3, id="two-trues-reject-h4"),
])
async def test_pair_review_requires_two_trues_for_general_chunk(answers, rejected, pair_calls, attempts):
    target, general = exchange_chunks()
    checked = []

    def check(inputs):
        answerable = answers[len(checked) % len(answers)]
        checked.append(inputs)
        return {"parsed": prompts.PairAnswer(answerable=answerable), "raw": "r"}

    samples, stats, calls = await synth_exchange(
        RunnableLambda(lambda _: {"parsed": LabelAnswer(answers=[1]), "raw": "r"}), RunnableLambda(check))
    if rejected:
        assert samples == []
    else:
        assert len(samples) == 1 and samples[0].keys == (target.key,)
    assert stats.rejected == rejected and stats.errors == 0
    assert calls == {"X3 退换规则": attempts}
    assert checked == [{"chunk_title": general.key, "chunk_text": general.text,
                        "query": "X3 换货只能换同款吗？", "model_info": "问题中的型号：X3（品类：蓝牙耳机）"}] * pair_calls


async def test_recall_rejects_h4_when_unrelated_general_chunk_can_answer():
    _, general = exchange_chunks()
    checked = []

    def check(inputs):
        checked.append(inputs["chunk_title"])
        return {"parsed": prompts.PairAnswer(answerable=inputs["chunk_title"] == general.key), "raw": "r"}

    samples, stats, calls = await synth_exchange(
        RunnableLambda(lambda _: {"parsed": LabelAnswer(answers=[1, 1]), "raw": "r"}), RunnableLambda(check))
    assert samples == []
    assert stats.rejected == {"通用段落也能回答": 3}
    assert stats.errors == 0
    assert checked == [general.key] * 6
    assert calls == {"X3 退换规则": 3}


async def test_recall_does_not_add_general_chunk_when_pair_review_is_false():
    checked = []

    def check(inputs):
        checked.append(inputs)
        return {"parsed": prompts.PairAnswer(answerable=False), "raw": "r"}

    samples, stats, _ = await synth_exchange(
        RunnableLambda(lambda _: {"parsed": LabelAnswer(answers=[1]), "raw": "r"}), RunnableLambda(check))
    target, general = exchange_chunks()
    assert len(samples) == 1 and samples[0].keys == (target.key,)
    assert stats.rejected == {} and stats.errors == 0
    assert checked == [{"chunk_title": general.key, "chunk_text": general.text,
                        "query": "X3 换货只能换同款吗？", "model_info": "问题中的型号：X3（品类：蓝牙耳机）"}]


@pytest.mark.parametrize("parsed, reason", [
    (None, "召回解析失败"),
    (LabelAnswer(answers=[2]), "召回编号无效"),
    (LabelAnswer(answers=[0]), "召回编号无效"),
])
async def test_recall_failure_rejects_attempt_before_pair_review(parsed, reason):
    def check(_):
        pytest.fail("召回失败后不应进行成对复核")

    samples, stats, calls = await synth_exchange(
        RunnableLambda(lambda _: {"parsed": parsed, "raw": "r"}), RunnableLambda(check))
    assert samples == []
    assert stats.rejected == {reason: 3} and stats.errors == 0
    assert calls == {"X3 退换规则": 3}


async def test_recall_receives_only_unlabeled_general_candidates():
    target, general = exchange_chunks()
    faq = Chunk("商品FAQ > 蓝牙耳机 > 如何换货？", "商品FAQ > 蓝牙耳机 > 如何换货？",
                "如何换货？", "申请换货的步骤", "蓝牙耳机", "商品FAQ", None, None)
    already_labeled = Chunk("售后手册 > 换货 > 换货流程", "售后手册 > 换货 > 换货流程",
                            "换货流程", "已入选的段落", "通用", "售后手册", None, None)
    other_category = Chunk("商品FAQ > 台灯 > 如何换货？", "商品FAQ > 台灯 > 如何换货？",
                           "如何换货？", "台灯申请换货", "台灯", "商品FAQ", None, None)
    received = []

    def recall(inputs):
        received.append(inputs)
        return {"parsed": LabelAnswer(answers=[]), "raw": "r"}

    gen, _ = gen_for({target.title: ["X3 换货可以换别的款吗？"]})
    samples, _ = await pipeline.synthesize(
        {"H1_sibling": [], "H2_colloquial": [Seed("H2_colloquial", target.key, 1, ())],
         "H3_buried": [], "H4_specific": []},
        [target, general, faq, already_labeled, other_category], LEX,
        generators={"H2_colloquial": gen},
        labeler=RunnableLambda(lambda _: {"parsed": LabelAnswer(answers=[1, 4]), "raw": "r"}),
        recall_labeler=RunnableLambda(recall), pair_checker=pair_checker_false(),
        per_bucket={"H1_sibling": 0, "H2_colloquial": 1, "H3_buried": 0, "H4_specific": 0})
    assert len(samples) == 1 and samples[0].keys == (target.key, already_labeled.key)
    assert received == [{"candidates": "[1] 售后手册 > 换货 > 申请换货\n换货只能换同款\n\n"
                                       "[2] 商品FAQ > 蓝牙耳机 > 如何换货？\n申请换货的步骤",
                         "query": "X3 换货可以换别的款吗？", "model_info": "问题中的型号：X3（品类：蓝牙耳机）"}]


async def test_recall_and_same_topic_reviews_merge_in_order_without_duplicates():
    _, general = exchange_chunks()
    faq = Chunk("商品FAQ > 蓝牙耳机 > 耳机充满电能用多久？", "商品FAQ > 蓝牙耳机 > 耳机充满电能用多久？",
                "耳机充满电能用多久？", "约 6 小时", "蓝牙耳机", "商品FAQ", None, None)
    checked = []

    def check(inputs):
        checked.append(inputs["chunk_title"])
        return {"parsed": prompts.PairAnswer(answerable=inputs["chunk_title"] in (general.key, faq.key)), "raw": "r"}

    gen, _ = gen_for({"X3 续航": ["X3 能用多久"]})
    samples, stats = await pipeline.synthesize(
        pools(1), CHUNKS + [general, faq], LEX,
        generators={"H1_sibling": gen}, labeler=labeler_target_only(),
        recall_labeler=RunnableLambda(lambda _: {"parsed": LabelAnswer(answers=[2, 1, 2]), "raw": "r"}),
        pair_checker=RunnableLambda(check),
        per_bucket={"H1_sibling": 1, "H2_colloquial": 0, "H3_buried": 0, "H4_specific": 0})
    assert len(samples) == 1 and samples[0].keys == (CHUNKS[0].key, faq.key, general.key)
    assert checked == [faq.key, faq.key, general.key, general.key, CHUNKS[1].key, CHUNKS[2].key]
    assert stats["H1_sibling"].rejected == {}


async def test_recall_is_skipped_when_no_unlabeled_general_candidates():
    def recall(_):
        pytest.fail("没有通用候选时不应调用召回")

    gen, _ = gen_for({"X3 续航": ["X3 能用多久"]})
    samples, _ = await pipeline.synthesize(
        pools(1), CHUNKS, LEX, generators={"H1_sibling": gen}, labeler=labeler_target_only(),
        recall_labeler=RunnableLambda(recall), pair_checker=pair_checker_false(),
        per_bucket={"H1_sibling": 1, "H2_colloquial": 0, "H3_buried": 0, "H4_specific": 0})
    assert len(samples) == 1 and samples[0].keys == (CHUNKS[0].key,)


def test_spotcheck_ids_deterministic():
    samples = [pipeline.HardSample(f"H{b}_{i:02d}", "x", "q", ("k",), "k", None)
               for b in range(1, 5) for i in range(1, 61)]
    ids = pipeline.spotcheck_ids(samples, 20261010)
    assert ids[:4] == ["H1_05", "H1_20", "H1_35", "H1_50"] and len(ids) == 20 and len(set(ids)) == 20
    assert ids == pipeline.spotcheck_ids(samples, 20261010)


def test_synthesis_code_does_not_import_retrieval_modules():
    code = ("import sys, importlib; importlib.import_module('scripts.synth_hard_set');"
            f"bad=[m for m in {FORBIDDEN!r} if m in sys.modules]; print(bad); sys.exit(1 if bad else 0)")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


async def test_cli_refuses_to_overwrite_frozen_set(tmp_path, monkeypatch, capsys):
    from scripts import synth_hard_set as cli
    frozen = tmp_path / "rag_eval_hard.jsonl"
    frozen.write_text("frozen\n", encoding="utf-8")
    monkeypatch.setattr(cli, "HARD_PATH", frozen)
    assert await cli.run(cli.build_parser().parse_args([])) == 1
    assert frozen.read_text(encoding="utf-8") == "frozen\n"
    assert "--force" in capsys.readouterr().out


async def test_cli_writes_nothing_when_bucket_short(tmp_path, monkeypatch):
    from scripts import synth_hard_set as cli
    monkeypatch.setattr(cli, "HARD_PATH", tmp_path / "rag_eval_hard.jsonl")
    monkeypatch.setattr(cli, "MANIFEST_PATH", tmp_path / "m.json")
    monkeypatch.setattr(cli, "SPOTCHECK_PATH", tmp_path / "s.md")

    async def fake_load(lexicon):
        return CHUNKS

    async def fake_synth(*a, **k):
        return [], {b: pipeline.BucketStats() for b in ("H1_sibling", "H2_colloquial", "H3_buried", "H4_specific")}

    async def noop():
        return None

    monkeypatch.setattr(cli, "load_chunks", fake_load)
    monkeypatch.setattr(cli, "synthesize", fake_synth)
    monkeypatch.setattr(cli, "build_runnables", lambda: ({}, None, recall_labeler_empty(), pair_checker_false()))
    monkeypatch.setattr(cli, "dispose_engine", noop)
    assert await cli.run(cli.build_parser().parse_args([])) == 1
    assert list(tmp_path.iterdir()) == []


async def test_cli_passes_max_attempts(tmp_path, monkeypatch):
    from scripts import synth_hard_set as cli
    monkeypatch.setattr(cli, "HARD_PATH", tmp_path / "rag_eval_hard.jsonl")
    monkeypatch.setattr(cli, "MANIFEST_PATH", tmp_path / "m.json")
    monkeypatch.setattr(cli, "SPOTCHECK_PATH", tmp_path / "s.md")
    received = {}
    recall = recall_labeler_empty()

    async def fake_load(lexicon):
        return CHUNKS

    async def fake_synth(*args, **kwargs):
        received.update(kwargs)
        samples = [pipeline.HardSample(f"H{n}_{i:02d}", bucket, f"问题 {n}-{i}",
                                       (CHUNKS[0].key,), CHUNKS[0].key, None)
                   for n, bucket in enumerate(cli.BUCKETS, start=1) for i in range(1, 61)]
        return samples, {b: pipeline.BucketStats() for b in cli.BUCKETS}

    async def noop():
        return None

    monkeypatch.setattr(cli, "load_chunks", fake_load)
    monkeypatch.setattr(cli, "build_runnables", lambda: ({}, None, recall, None))
    monkeypatch.setattr(cli, "dispose_engine", noop)
    monkeypatch.setattr(cli, "synthesize", fake_synth)
    assert await cli.run(cli.build_parser().parse_args([])) == 0
    assert received.get("recall_labeler") is recall
    assert received.get("max_attempts") == 5
    assert json.loads(cli.MANIFEST_PATH.read_text(encoding="utf-8"))["max_attempts"] == 5


async def test_cli_check_missing_file(tmp_path, monkeypatch, capsys):
    from scripts import synth_hard_set as cli
    monkeypatch.setattr(cli, "HARD_PATH", tmp_path / "rag_eval_hard.jsonl")

    async def fail_load(lexicon):
        pytest.fail("文件缺失时不应调用 load_chunks")

    monkeypatch.setattr(cli, "load_chunks", fail_load)
    assert await cli.run(cli.build_parser().parse_args(["--check"])) == 1
    assert "未找到" in capsys.readouterr().out


async def test_cli_check_detects_hash_mismatch(tmp_path, monkeypatch, capsys):
    from scripts import synth_hard_set as cli
    path, manifest = tmp_path / "rag_eval_hard.jsonl", tmp_path / "m.json"
    path.write_text(json.dumps({"id": "H1_01"}) + "\n", encoding="utf-8")
    manifest.write_text(json.dumps({"jsonl_sha256": "0" * 64}), encoding="utf-8")
    monkeypatch.setattr(cli, "HARD_PATH", path)
    monkeypatch.setattr(cli, "MANIFEST_PATH", manifest)
    assert cli.check_hash() == "SHA-256 与 manifest 不一致"
