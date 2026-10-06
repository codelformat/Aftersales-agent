"""用标注样例评估检索结果，并扫描相似度阈值。"""

import argparse
import asyncio
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import sys

# 用脚本位置定位项目和样例文件。
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from app.config import FAQ_MIN_SCORE
from app.db.engine import dispose_engine
from app.knowledge.milvus import close_milvus, ensure_collection
from app.knowledge.retrieval import search_with_scores

logger = logging.getLogger(__name__)
SAMPLES_PATH = SCRIPT_DIR / "retrieval_samples.jsonl"


@dataclass
class SampleResult:
    expected_questions: list[str]
    hits: list[tuple[str, float]]

    def passed(self, min_score: float) -> bool:
        questions = [question for question, score in self.hits if score >= min_score]
        if self.expected_questions:
            return any(question in self.expected_questions for question in questions)
        return not questions


def report_rates(results: list[SampleResult], min_score: float) -> tuple[float, float]:
    relevant = [result for result in results if result.expected_questions]
    unrelated = [result for result in results if not result.expected_questions]
    relevant_correct = sum(result.passed(min_score) for result in relevant)
    unrelated_correct = sum(result.passed(min_score) for result in unrelated)
    relevant_rate = relevant_correct / len(relevant)
    unrelated_rate = unrelated_correct / len(unrelated)
    print(f"Top 3 命中率: {relevant_rate:.2%} ({relevant_correct}/{len(relevant)})")
    print(f"无关样例通过率: {unrelated_rate:.2%} ({unrelated_correct}/{len(unrelated)})")
    return relevant_rate, unrelated_rate


async def run_eval() -> int:
    try:
        await ensure_collection()
        samples = [
            json.loads(line)
            for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not samples:
            raise ValueError("样例集为空")
        if not any(sample["expected_questions"] for sample in samples) or not any(
            not sample["expected_questions"] for sample in samples
        ):
            raise ValueError("样例集须包含相关和无关样例")

        results = []
        for index, sample in enumerate(samples, start=1):
            hits = await search_with_scores(sample["keyword"], limit=3, min_score=0.0)
            result = SampleResult(
                expected_questions=sample["expected_questions"],
                hits=[(chunk.questions, score) for chunk, score in hits],
            )
            results.append(result)
            marker = "✅" if result.passed(FAQ_MIN_SCORE) else "❌"
            print(f"{index:02d} {marker} {sample['keyword']!r}")
            for rank, (question, score) in enumerate(result.hits, start=1):
                print(f"  {rank}. {question!r} | 相似度: {score:.3f}")
            if not result.hits:
                print("  无结果")

        print(f"当前阈值: {FAQ_MIN_SCORE:.2f}")
        relevant_rate, unrelated_rate = report_rates(results, FAQ_MIN_SCORE)
        print("阈值扫描")
        # 用整数生成阈值，避免累加小数产生误差。
        for value in range(40, 66, 5):
            threshold = value / 100
            print(f"阈值: {threshold:.2f}")
            report_rates(results, threshold)
        return 0 if relevant_rate >= 0.9 and unrelated_rate >= 0.8 else 1
    finally:
        # 即使 Milvus 关闭失败，也要尝试释放数据库引擎。
        try:
            await close_milvus()
        finally:
            await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="评估检索样例，正常运行会调用嵌入接口、Milvus 和 MySQL。"
    )
    parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    try:
        return asyncio.run(run_eval())
    except Exception:
        logger.exception("检索评估执行失败")
        print("检索评估执行失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
