import json
from pathlib import Path

import pytest
from langchain_core.runnables import RunnableLambda

from app.schemas import CoverageVerdict, GroupCoverage
from evals import run_coverage_judge_eval as cje

ROOT = Path(__file__).resolve().parent.parent


def write(tmp_path, samples):
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in samples), encoding="utf-8")
    return path


def sample(i, covered):
    return {"id": f"V{i:02d}", "question": str(i), "answer": str(i), "covered": covered, "cases": [],
            "references": [{"key": f"k{n}", "text": f"t{n}"} for n in range(1, len(covered) + 1)]}


@pytest.mark.anyio
@pytest.mark.parametrize("wrong, fail_one, bad_numbers, expected", [
    (0, False, False, 0), (1, False, False, 0), (3, False, False, 1),
    (0, True, False, 1), (0, False, True, 1),
])
async def test_group_accuracy_and_failures(monkeypatch, tmp_path, capsys, wrong, fail_one, bad_numbers, expected):
    # 10 题共 20 组。错 1 组为 95%，错 3 组为 85%。
    samples = [sample(i, [True, True]) for i in range(10)]
    monkeypatch.setattr(cje, "SAMPLES_PATH", write(tmp_path, samples))
    calls = []

    def run(inputs):
        calls.append(inputs)
        i = int(inputs["answer"])
        if fail_one and i == 0:
            raise RuntimeError("x")
        if bad_numbers and i == 0:
            return {"parsed": CoverageVerdict(groups=[GroupCoverage(n=1, covered=True, reason="r")])}
        groups = [GroupCoverage(n=n, covered=not (i < wrong and n == 1), reason="r") for n in (1, 2)]
        return {"parsed": CoverageVerdict(groups=groups)}

    monkeypatch.setattr(cje, "get_coverage_judge", lambda: RunnableLambda(run))
    assert await cje.run_eval() == expected and len(calls) == 10
    assert calls[0]["references"] == "[1] k1\nt1\n\n[2] k2\nt2"
    assert "覆盖率裁判准确率" in capsys.readouterr().out


def corpus() -> str:
    parts = [p.read_text(encoding="utf-8") for p in sorted((ROOT / "knowledge/docs").rglob("*.md"))]
    parts.append((ROOT / "db/seed.sql").read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_samples_file_shape():
    rows = [json.loads(line) for line in cje.SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [r["id"] for r in rows] == [f"V{i:02d}" for i in range(1, 31)]
    assert sum(len(r["covered"]) for r in rows) >= 60
    for r in rows:
        keys = [ref["key"] for ref in r["references"]]
        assert len(keys) == len(set(keys)) == len(r["covered"]) >= 1
        assert all(isinstance(c, bool) for c in r["covered"])
        assert set(r["cases"]) <= set(cje.CASE_LABELS) and r["cases"]
    for label in cje.CASE_LABELS:
        assert sum(label in r["cases"] for r in rows) >= 3, label


def test_samples_do_not_copy_v2_questions():
    v2 = {json.loads(line)["query"] for line in (ROOT / "evals/rag_eval_v2.jsonl").read_text(encoding="utf-8").splitlines()
          if line.strip()}
    rows = [json.loads(line) for line in cje.SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert not [r["id"] for r in rows if r["question"] in v2]


def test_reference_lines_come_from_knowledge_sources():
    text = corpus()
    rows = [json.loads(line) for line in cje.SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    missing = [(r["id"], line) for r in rows for ref in r["references"]
               for line in ref["text"].splitlines() if line.strip() and line.strip() not in text]
    assert not missing
