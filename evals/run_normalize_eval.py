"""用标注样例评估问题标准化，正常运行会调用真实上游。"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
import re
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from app.flywheel.pipeline import ensure_question_mark, format_chunks
from app.llm import get_question_normalizer
from app.schemas import NormalizedQuestion

logger = logging.getLogger(__name__)
SAMPLES_PATH = SCRIPT_DIR / "normalize_samples.jsonl"


async def evaluate_sample(
    index: int, sample: dict, semaphore: asyncio.Semaphore
) -> tuple[bool, bool, str]:
    async with semaphore:
        try:
            result = await get_question_normalizer().ainvoke({
                "question": sample["question"],
                "chunks": format_chunks(sample["chunks"]),
            })
            parsed = result["parsed"]
            if parsed is None:
                raise ValueError(f"结果无法解析：{result.get('parsing_error')}")
            actual = NormalizedQuestion.model_validate(parsed)
        except Exception as exc:
            logger.exception("样例 %d 标准化评估失败", index)
            return False, False, f"{index:02d} ❌ {sample['question']!r} | 调用或解析失败：{exc}"

    question = ensure_question_mark(actual.normalized_question)
    answer = actual.suggested_answer
    failures = []
    missing = [word for word in sample["must_keep"] if word not in question]
    forbidden = [word for word in sample["must_drop"] if word in question]
    if missing:
        failures.append(f"缺少必留词：{missing!r}")
    if forbidden:
        failures.append(f"含禁止词：{forbidden!r}")
    if not question.endswith("？"):
        failures.append("未以中文问号结尾")
    if len(question) > 60:
        failures.append(f"问题超长：{len(question)} 字 > 60")
    if not answer.startswith("（待核实）"):
        failures.append("答案缺少（待核实）前缀")
    # 数字依据来自片段答案或用户原话，不取片段序号、分数等元数据。
    supported = set(re.findall(r"\d+", sample["question"]))
    supported.update(
        number
        for chunk in sample["chunks"] or []
        for number in re.findall(r"\d+", chunk["answer"])
    )
    unsupported = sorted(set(re.findall(r"\d+", answer)) - supported)
    if unsupported:
        failures.append(f"答案数字无片段或用户原话依据：{unsupported!r}")

    passed = not failures
    reason = "；".join(failures) if failures else "全部检查通过"
    line = (
        f"{index:02d} {'✅' if passed else '❌'} {sample['question']!r}"
        f" | {reason}\n  问题：{question}\n  答案：{answer}"
    )
    return passed, True, line


async def run_eval() -> int:
    samples = [
        json.loads(line)
        for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not samples:
        raise ValueError("样例集为空")
    semaphore = asyncio.Semaphore(3)
    results = await asyncio.gather(
        *(evaluate_sample(index, sample, semaphore)
          for index, sample in enumerate(samples, start=1))
    )
    for _, _, line in results:
        print(line)
    total = len(results)
    parsed = sum(result[1] for result in results)
    passed = sum(result[0] for result in results)
    print(f"JSON 解析率：{parsed / total:.2%} ({parsed}/{total})")
    print(f"标准化全部检查通过率：{passed / total:.2%} ({passed}/{total})")
    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    try:
        return asyncio.run(run_eval())
    except Exception:
        logger.exception("标准化评估执行失败")
        print("标准化评估执行失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
