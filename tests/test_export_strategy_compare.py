import importlib
import json
from copy import deepcopy
from pathlib import Path


def exporter():
    return importlib.import_module("evals.export_strategy_compare")


def fixture_data():
    samples = {sid: {"query": f"{sid} question", "bucket": bucket, "relevant": [["right", "alternate"]]}
               for sid, bucket in (("C01", "C_colloquial"), ("C02", "C_colloquial"),
                                   ("B01", "B_model"), ("B02", "B_model"),
                                   ("A01", "A_policy"), ("A02", "A_policy"))}
    rankings = {s: {sid: ["alternate", "wrong"] for sid in samples}
                for s in ("dense", "bm25", "hybrid", "hybrid_rerank")}
    rankings["hybrid_rerank"]["C01"] = ["wrong"]
    rankings["bm25"]["C02"] = ["wrong", "right"]
    rankings["dense"]["B02"] = ["wrong", "right"]
    rankings["hybrid"]["A01"] = []
    return {"samples": samples, "rankings": rankings}


def test_representatives_first_matching_in_each_bucket_and_group_relevance():
    data = fixture_data()
    original = deepcopy(data)
    cases = exporter().select_cases(data)
    assert [c["id"] for c in cases] == ["C02", "B02", "A02"]
    assert cases[0]["rankings"]["bm25"] == [{"key": "wrong", "relevant": False},
                                               {"key": "right", "relevant": True}]
    assert cases[1]["rankings"]["hybrid_rerank"][0] == {"key": "alternate", "relevant": True}
    assert not any(c.get("fallback") for c in cases)
    assert data == original


def test_representatives_fallback_same_bucket_first_and_note():
    data = fixture_data()
    data["rankings"]["bm25"]["C02"] = ["right"]
    data["rankings"]["dense"]["B02"] = ["right"]
    data["rankings"]["hybrid"]["A02"] = []
    cases = exporter().select_cases(data)
    assert [c["id"] for c in cases] == ["C01", "B01", "A01"]
    assert all(c["fallback"] and c["selection_note"] for c in cases)


def test_export_metrics_from_report_and_cli_provenance(tmp_path):
    data = fixture_data()
    rankings = tmp_path / "rankings.json"
    rankings.write_text(json.dumps(data))
    report = Path(__file__).resolve().parents[1] / "evals/reports/rag_eval_20261009-193611.md"
    out = tmp_path / "nested/out.json"
    assert exporter().main(["--rankings", str(rankings), "--report", str(report), "--out", str(out)]) == 0
    result = json.loads(out.read_text())
    assert result["metrics"]["dense"] == {"R@1": .844, "R@3": .965, "R@5": .985,
                                             "R@10": .994, "MRR": .969, "faithfulness": .933,
                                             "false_refusal": .054, "d_refusal": .983}
    assert result["metrics"]["hybrid_rerank"]["R@1"] == .861
    assert result["metrics"]["hybrid_rerank"]["faithfulness"] == .964
    assert result["generated_from"]["report"] == str(report)
    assert result["generated_from"]["rankings"] == str(rankings)
    assert len(result["generated_from"]["git_commit"]) == 40
    assert result["generated_from"]["metrics_source"] == "report"
    assert result["generated_from"]["consistency_note"]
