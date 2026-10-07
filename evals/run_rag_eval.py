"""按检索和生成两段运行 RAG 评估，并保存报告。"""

import argparse
import asyncio
from dataclasses import asdict
from datetime import date, datetime
import json
import logging
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable

from app.config import RERANK_MIN_SCORE, get_settings
from app.db.engine import dispose_engine, get_sessionmaker
from app.knowledge.milvus import close_milvus, ensure_collection
from app.knowledge.query import understand
from app.knowledge.rerank import close_rerank
from app.knowledge.retrieval import STRATEGIES, retrieve, source_key
from app.llm import get_chat_model, get_faith_judge
from app.prompts import TOOL_ROUND_CLOSING, chat_prompt, chat_prompt_vars
from app.repositories import faith_cases
from app.schemas import FaithVerdict, QueryPlan
from app.services.grounding import (
    REFUSED_CONTENT, collect_evidence, format_evidence, render_evidence, self_check,
)
from app.tools.registry import get_registry
from evals.rag_eval_set import BUCKET_SIZES, EvalSample, known_source_keys, load_samples, validate
from evals.rag_metrics import (
    ANSWERABLE, GenResult, ThresholdRow, is_refusal, render_report, score_retrieval,
    summarize, threshold_sweep,
)

logger = logging.getLogger(__name__)
REPORTS_DIR = SCRIPT_DIR / "reports"
EVAL_SKIPPED_TOOL = json.dumps(
    {"ok": False, "error": "tool_error", "message": "查询失败"}, ensure_ascii=False
)


async def _stream(runnable, inputs):
    gathered = None
    async for chunk in runnable.astream(inputs):
        gathered = chunk if gathered is None else gathered + chunk
    if gathered is None:
        raise ValueError("模型返回空流")
    return gathered


async def generate_one(
    sample: EvalSample, plan: QueryPlan, strategy: str, *, model: BaseChatModel,
    judge: Runnable, checker: Runnable | None = None, today: date,
) -> GenResult:
    """沿用首轮模型给出的工具调用 id。第 2 次调用不绑定工具。"""
    base = {**chat_prompt_vars(today), "history": [], "input": sample.query}
    first = await _stream(
        chat_prompt | model.bind_tools(get_registry().tools_for_model(), tool_choice="auto"), base,
    )
    first_text = first.content if isinstance(first.content, str) else ""
    faq_calls = [c for c in first.tool_calls if c["name"] == "query_faq"]
    if not faq_calls:
        return GenResult(sample.id, sample.bucket, sample.difficulty, strategy, sample.query,
                         False, is_refusal(first_text), first_text, [], None, [], "")
    r = await retrieve(sample.query, strategy, plan=plan, exclude_mined=True)
    data = {"evidence": [asdict(e) for e in r.evidence]}
    evidence = collect_evidence([(c["id"], sample.query, data) for c in faq_calls])
    check = await self_check([sample.query], evidence.citations, checker=checker)
    messages = []
    for call in first.tool_calls:
        if call["name"] != "query_faq":
            content = EVAL_SKIPPED_TOOL
        elif check.useful:
            content = render_evidence(evidence.by_call[call["id"]])
        else:
            content = REFUSED_CONTENT
        messages.append(ToolMessage(content=content, tool_call_id=call["id"], name=call["name"]))
    request = AIMessage(content=first_text, tool_calls=first.tool_calls)
    second = await _stream(chat_prompt | model, {
        **base, "tool_round": [request, *messages, SystemMessage(TOOL_ROUND_CLOSING)],
    })
    answer = second.content if isinstance(second.content, str) else ""
    citations = [c.to_dict() for c in evidence.citations] if check.useful else []
    refused = is_refusal(answer)
    if refused:
        return GenResult(sample.id, sample.bucket, sample.difficulty, strategy, sample.query,
                         True, True, answer, citations, None, [], check.reason)
    shown = evidence.citations if check.useful else []
    try:
        result = await judge.ainvoke({"evidence": format_evidence(shown) or "（无证据）", "answer": answer})
        if result["parsed"] is None:
            raise ValueError("裁判结果无效")
        verdict = FaithVerdict.model_validate(result["parsed"])
    except Exception:
        logger.exception("裁判失败：%s/%s", sample.id, strategy)
        return GenResult(sample.id, sample.bucket, sample.difficulty, strategy, sample.query,
                         True, False, answer, citations, None, [], "裁判失败")
    return GenResult(sample.id, sample.bucket, sample.difficulty, strategy, sample.query,
                     True, False, answer, citations, verdict.faithful, verdict.unsupported_claims, verdict.reason)


async def write_faith_cases(results: list[GenResult], judge_model: str) -> int:
    count = 0
    for r in results:
        if not (r.strategy == "hybrid_rerank" and r.bucket in ANSWERABLE
                and r.retrieved and not r.refused and r.faithful is False):
            continue
        async with get_sessionmaker()() as s:
            await faith_cases.upsert_case(
                s, eval_id=r.sample_id, bucket=r.bucket, query=r.query, strategy=r.strategy,
                answer=r.answer, reason=f"{r.reason}\n未找到依据：{'；'.join(r.unsupported)}",
                citations=r.citations, judge_model=judge_model,
            )
            await s.commit()
        count += 1
    return count


async def run_eval(args: argparse.Namespace) -> int:
    try:
        await ensure_collection()
        keys = await known_source_keys()
        if args.list_keys:
            for key, preview in keys.items():
                print(f"{key}\t{preview}")
            return 0
        all_samples = load_samples()
        errors = validate(all_samples, set(keys), full=True)
        if errors:
            for error in errors:
                print(error)
            return 1
        if args.check:
            print("评估集检查通过")
            return 0
        samples = [s for s in all_samples if args.bucket is None or s.bucket == args.bucket]
        if args.limit is not None:
            samples = samples[:args.limit]
        semaphore = asyncio.Semaphore(args.concurrency)
        failures = []
        plans = {}

        async def prepare(sample):
            async with semaphore:
                try:
                    plans[sample.id] = await understand(sample.query)
                except Exception as exc:
                    logger.exception("问题理解失败：%s", sample.id)
                    failures.append(f"{sample.id}/understand：{type(exc).__name__}")

        await asyncio.gather(*(prepare(s) for s in samples))
        scores, post_scores, rows = [], [], []
        do_retrieval = args.stage in ("retrieval", "all")
        do_generation = args.stage in ("generation", "all")

        async def retrieve_one(sample, strategy):
            async with semaphore:
                try:
                    r = await retrieve(sample.query, strategy, plan=plans[sample.id], exclude_mined=True)
                    ranked = [source_key(e.section_path, e.question) for e in r.ranked]
                    if sample.bucket in ANSWERABLE:
                        scores.append(score_retrieval(sample.id, sample.bucket, sample.difficulty,
                                                      strategy, ranked, sample.relevant))
                    if strategy == "hybrid_rerank":
                        rows.append(ThresholdRow(sample.bucket, bool(ranked) and ranked[0] in sample.relevant,
                                                 r.ranked[0].score if r.ranked else None))
                        if sample.bucket in ANSWERABLE:
                            kept = [source_key(e.section_path, e.question) for e in r.ranked
                                    if e.score >= RERANK_MIN_SCORE]
                            post_scores.append(score_retrieval(sample.id, sample.bucket, sample.difficulty,
                                                               strategy, kept, sample.relevant))
                except Exception as exc:
                    logger.exception("检索评估失败：%s/%s", sample.id, strategy)
                    failures.append(f"{sample.id}/{strategy}：{type(exc).__name__}")

        if do_retrieval:
            await asyncio.gather(*(retrieve_one(s, strategy) for s in samples if s.id in plans
                                   for strategy in STRATEGIES))

        results = []
        if do_generation:
            model, judge = get_chat_model(), get_faith_judge()
            strategies = STRATEGIES if args.gen_strategies == "all" else ("hybrid_rerank",)
            today = date.today()

            async def generate(sample, strategy):
                async with semaphore:
                    try:
                        result = await generate_one(sample, plans[sample.id], strategy,
                                                    model=model, judge=judge, today=today)
                        if result.retrieved and not result.refused and result.faithful is None:
                            failures.append(f"{sample.id}/{strategy}：裁判失败")
                        return result
                    except Exception as exc:
                        logger.exception("生成评估失败：%s/%s", sample.id, strategy)
                        failures.append(f"{sample.id}/{strategy}：{type(exc).__name__}")
                        return None

            generated = await asyncio.gather(*(generate(s, strategy) for s in samples if s.id in plans
                                              for strategy in strategies))
            results = [r for r in generated if r is not None]
            if not args.no_write:
                try:
                    count = await write_faith_cases(results, get_settings().chat_model)
                    print(f"编造个案写入条数：{count}")
                except Exception as exc:
                    logger.exception("编造个案写入失败")
                    failures.append(f"faith_cases：{type(exc).__name__}")

        # 并发完成顺序不影响报告中组名的顺序。
        scores.sort(key=lambda s: (STRATEGIES.index(s.strategy), s.sample_id))
        post_scores.sort(key=lambda s: s.sample_id)
        failures.sort()
        report = render_report(
            retrieval_by_bucket=summarize(scores, "bucket") if do_retrieval else None,
            retrieval_by_difficulty=summarize(scores, "difficulty") if do_retrieval else None,
            post_threshold=summarize(post_scores, "bucket") if do_retrieval else None,
            sweep=threshold_sweep(rows) if do_retrieval else None, current_threshold=RERANK_MIN_SCORE,
            generation=results if do_generation else None, failures=failures,
        )
        params = json.dumps(vars(args), ensure_ascii=False, sort_keys=True)
        report = (f"# RAG 评估报告\n\n运行参数：`{params}`\n\n"
                  f"评估集题数：{len(all_samples)}；本次题数：{len(samples)}\n\n" + report)
        print(report)
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        report_path = REPORTS_DIR / f"rag_eval_{datetime.now():%Y%m%d-%H%M%S}.md"
        report_path.write_text(report, encoding="utf-8")
        return 1 if failures else 0
    finally:
        try:
            await close_milvus()
        finally:
            try:
                await close_rerank()
            finally:
                await dispose_engine()


def _positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("参数必须为正整数")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="评估 RAG，正常运行会调用上游模型并写入编造个案。")
    parser.add_argument("--stage", choices=("retrieval", "generation", "all"), default="all")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--list-keys", action="store_true")
    parser.add_argument("--gen-strategies", choices=("hybrid_rerank", "all"), default="hybrid_rerank")
    parser.add_argument("--concurrency", type=_positive, default=8)
    parser.add_argument("--limit", type=_positive)
    parser.add_argument("--bucket", choices=tuple(BUCKET_SIZES))
    parser.add_argument("--no-write", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.ERROR)
    try:
        return asyncio.run(run_eval(args))
    except Exception:
        logger.exception("RAG 评估执行失败")
        print("RAG 评估执行失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
