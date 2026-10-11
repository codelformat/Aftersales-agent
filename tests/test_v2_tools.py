import json
from collections import Counter

import pytest

from evals.v2 import check_d_evidence as cd, make_targets as mt, new_chunk_audit as na

CATS = {"X3": "蓝牙耳机", "X3 Pro": "蓝牙耳机", "X5": "蓝牙耳机", "X3 Lite": "蓝牙耳机", "X5 Pro": "蓝牙耳机"}
SECTIONS = ("参数", "续航", "保修", "退换规则", "常见故障")


def fake_keys():
    keys = [f"商品手册 > 蓝牙耳机 > {m} {s}" for m in CATS for s in SECTIONS]
    keys += [f"退货政策 > 退货条件 > 条款{i}" for i in range(5)]
    keys += [f"配送与签收政策 > 发货 > 条款{i}" for i in range(3)]
    keys += [f"预售与缺货政策 > 预售 > 条款{i}" for i in range(3)]
    keys += ["商品FAQ > 蓝牙耳机 > 耳机怎么连手机？", "商品FAQ > 配送与签收 > 下单后多久能发货啊？",
             "飞轮 > 某问题", "mined > x", "飞轮补充 > 某问题", "对话挖掘 > 某问题"]
    keys += [f"常见问答 > 问题{i}" for i in range(20)]
    return keys


def old_keys():
    return [k for k in fake_keys() if "X3 Lite" not in k and "X5 Pro" not in k and "配送" not in k
            and "预售" not in k]


def build(seed=mt.SEED):
    return mt.build_targets(fake_keys(), old_keys(), CATS, seed=seed)


def test_targets_counts_and_determinism():
    rows = build()
    assert rows == build()
    assert json.dumps(rows, ensure_ascii=False) == json.dumps(build(), ensure_ascii=False)
    assert rows != build(mt.SEED + 1)
    assert Counter(r["bucket"] for r in rows) == {"A_policy": 40, "B_model": 50, "C_colloquial": 50,
                                                  "E_multi": 100, "D_unanswerable": 60}
    diffs = Counter((r["bucket"], r["difficulty"]) for r in rows)
    assert diffs[("A_policy", "easy")] == 0 and diffs[("E_multi", "hard")] == 40
    assert sum(1 for r in rows if r["bucket"] == "E_multi" and len(r["targets"]) == 3) >= 50
    assert Counter(r["d_class"] for r in rows if r["bucket"] == "D_unanswerable") == {1: 15, 2: 15, 3: 15, 4: 15}


def test_targets_exclude_foreign_roots_and_cover_models():
    rows = build()
    used = {k for r in rows for g in r["targets"] for k in g}
    assert not any(k.startswith(("飞轮", "mined", "对话挖掘")) for k in used)
    def model_of(key):
        last = key.split(" > ")[-1]
        return next(m for m in sorted(CATS, key=len, reverse=True) if last.startswith(m + " "))

    b_rows = [r for r in rows if r["bucket"] == "B_model"]
    assert {model_of(r["targets"][0][0]) for r in b_rows} == set(CATS)
    for r in b_rows:
        assert r["distractor"] in CATS and r["distractor"] != model_of(r["targets"][0][0])


def test_c_bucket_includes_new_faq():
    rows = build()
    c_keys = {r["targets"][0][0] for r in rows if r["bucket"] == "C_colloquial"}
    assert "商品FAQ > 配送与签收 > 下单后多久能发货啊？" in c_keys
    assert len(c_keys) == 50


def test_load_keys_ignores_multiline_previews_and_dedupes_in_order(tmp_path):
    path = tmp_path / "keys.tsv"
    path.write_text("商品手册 > 蓝牙耳机 > X3 参数\t预览\t剩余预览\n"
                    "预览的下一行\n\n"
                    "退货政策 > 退货条件 > 条款0\t另一段预览\n"
                    "商品手册 > 蓝牙耳机 > X3 参数\t重复键\n", encoding="utf-8")
    assert mt.load_keys(path) == ["商品手册 > 蓝牙耳机 > X3 参数", "退货政策 > 退货条件 > 条款0"]


def test_targets_sampling_rules():
    rows = build()
    models = sorted(CATS, key=len, reverse=True)

    def model_section(key):
        last = key.split(" > ")[-1]
        model = next(m for m in models if last.startswith(m + " "))
        return model, last[len(model) + 1:]

    a_keys = [r["targets"][0][0] for r in rows if r["bucket"] == "A_policy"]
    pool = [k for k in fake_keys() if k.split(" > ")[0] in mt.POLICY_ROOTS
            or k.startswith(("商品FAQ > 配送与签收 > ", "商品FAQ > 预售与缺货 > "))]
    assert set(a_keys[:len(pool)]) == set(pool)
    b_rows = [r for r in rows if r["bucket"] == "B_model"]
    assert {model_section(r["targets"][0][0])[0] for r in b_rows[:len(CATS)]} == set(CATS)
    assert all(model_section(r["targets"][0][0])[0] in ("X3 Lite", "X5 Pro")
               for r in b_rows[len(CATS):])
    for row in b_rows:
        model, _ = model_section(row["targets"][0][0])
        if model not in ("X3 Lite", "X5 Pro"):
            assert row["distractor"] in ("X3 Lite", "X5 Pro")
    e_rows = [r for r in rows if r["bucket"] == "E_multi"]
    for row in e_rows[:25]:
        pairs = [model_section(g[0]) for g in row["targets"]]
        assert len({m for m, _ in pairs}) == 3
        assert len({s for _, s in pairs}) == 1
        assert any(m in ("X3 Lite", "X5 Pro") for m, _ in pairs)
    for row in e_rows[25:50]:
        assert row["targets"][0][0].startswith("商品手册 > ")
        roots = {g[0].split(" > ")[0] for g in row["targets"][1:]}
        assert len(roots) == 2 and roots <= set(mt.POLICY_ROOTS)
    for row in e_rows[50:]:
        assert len(row["targets"]) == 2
        assert len({g[0].split(" > ")[0] for g in row["targets"]}) == 2
    expected = {"A_policy": (0, 20, 20), "B_model": (10, 20, 20), "C_colloquial": (10, 20, 20),
                "D_unanswerable": (15, 20, 25), "E_multi": (20, 40, 40)}
    for bucket, sizes in expected.items():
        bucket_rows = [r for r in rows if r["bucket"] == bucket]
        assert [r["id"] for r in bucket_rows] == [f"{bucket[0]}{i:03d}" for i in range(1, len(bucket_rows) + 1)]
        assert tuple(sum(r["difficulty"] == d for r in bucket_rows) for d in ("easy", "medium", "hard")) == sizes
    assert all(r["targets"] == [] for r in rows if r["bucket"] == "D_unanswerable")


def test_targets_reject_insufficient_unique_c_pool():
    with pytest.raises(ValueError, match="C"):
        mt.build_targets(fake_keys()[:25], old_keys(), CATS)


def test_count_hits_literal_case_insensitive():
    assert cd.count_hits("x5 lite", ["X5 Lite 的参数"]) == 1
    assert cd.count_hits("(", ["a(b"]) == 1 and cd.count_hits("毫安", ["容量很小"]) == 0


def test_d_evidence_check():
    texts = ["X3 使用蓝牙 5.2", "锂电池容量很小"]
    ev = [{"set": "v2", "id": "D001", "keywords": ["毫安"]},
          {"set": "base", "id": "D25", "keywords": ["电池容量"]}]
    errors = "\n".join(cd.check(ev, {"v2": {"D001", "D002"}, "base": {"D25"}}, texts))
    assert "D25" in errors and "电池容量" in errors and "D002" in errors and "D001：" not in errors


def test_d_evidence_requires_exactly_one_row_per_set_and_id():
    row = {"set": "v2", "id": "D001", "keywords": ["不存在"]}
    assert cd.check([row, {**row, "set": "base"}], {"v2": {"D001"}, "base": {"D001"}}, []) == []
    errors = "\n".join(cd.check([row, row, {**row, "id": "A001"}, {**row, "set": "other"}],
                                {"v2": {"D001"}, "base": {"D25"}}, []))
    assert all(word in errors for word in ("D001", "A001", "other", "D25"))


@pytest.mark.parametrize("keywords", [[], ["a"] * 5, "a", [""], ["  "], [1], None])
def test_d_evidence_rejects_invalid_keywords(keywords):
    assert cd.check([{"set": "v2", "id": "D001", "keywords": keywords}], {"v2": {"D001"}}, [])


def test_audit_skips_d_and_missing_and_dedupes():
    rankings = {
        "rankings": {"hybrid_rerank": {"A01": ["new", "new", "old"], "A02": ["old", "new"], "D01": ["new"]}},
        "samples": {"A01": {"relevant": [["old"]], "bucket": "A_policy", "query": "q1"},
                    "A02": {"relevant": [["old"]], "bucket": "A_policy", "query": "q2"},
                    "A03": {"relevant": [["old"]], "bucket": "A_policy", "query": "q3"},
                    "D01": {"relevant": [], "bucket": "D_unanswerable", "query": "q4"}},
    }
    rows = na.audit(rankings, {"new"})
    assert [(r["id"], r["new_rank"], r["first_hit_rank"]) for r in rows] == [("A01", 1, 2)]
    assert "A01" in na.render(rows)


def test_audit_top_five_and_equivalent_keys_and_strategy_filter():
    data = {"rankings": {"dense": {"A01": ["x", "x", "x", "x", "x", "new"],
                                    "A02": ["x", "y", "z", "w", "v", "new"],
                                    "A03": ["equivalent", "new"], "A04": ["new", "new"]},
                         "bm25": {"A04": ["new"]}},
            "samples": {id: {"relevant": [["old", "equivalent"], ["another"]],
                             "bucket": "A_policy", "query": "q|1\nnext"}
                        for id in ("A01", "A02", "A03", "A04")}, "failures": []}
    rows = na.audit(data, {"new"}, strategies=["dense"])
    assert [(r["strategy"], r["id"], r["new_rank"], r["first_hit_rank"]) for r in rows] == [
        ("dense", "A01", 2, None), ("dense", "A04", 1, None)]
    assert "q\\|1 next" in na.render(rows)
    assert na.audit(data, {"equivalent"}, strategies=["dense"]) == []


def test_make_targets_cli_writes_identical_jsonl(tmp_path, monkeypatch):
    from app.knowledge.query import load_lexicon

    lexicon = tmp_path / "lexicon.json"
    lexicon.write_text(json.dumps({"models": {"蓝牙耳机": list(CATS)}, "synonyms": {}}), encoding="utf-8")
    monkeypatch.setattr(mt, "load_lexicon", lambda: load_lexicon(lexicon))
    keys, old = tmp_path / "new.tsv", tmp_path / "old.tsv"
    for path, values in ((keys, fake_keys()), (old, old_keys())):
        path.write_text("".join(f"{k}\t预览\n" for k in values), encoding="utf-8")
    out = tmp_path / "nested" / "targets.jsonl"
    monkeypatch.setattr("sys.argv", ["make_targets", "--keys", str(keys), "--old-keys", str(old), "--out", str(out)])
    assert mt.main() == 0
    first = out.read_bytes()
    assert [json.loads(line) for line in first.splitlines()] == build()
    assert mt.main() == 0 and out.read_bytes() == first


@pytest.mark.parametrize("explicit_seed", [True, False])
def test_check_d_evidence_cli_includes_seed_text(tmp_path, monkeypatch, capsys, explicit_seed):
    docs = tmp_path / "knowledge" / "docs" / "nested"
    docs.mkdir(parents=True)
    (docs / "manual.md").write_text("商品参数", encoding="utf-8")
    seed = tmp_path / "db" / "seed.sql"
    seed.parent.mkdir()
    seed.write_text("-- FAQ_ONLY\nINSERT INTO faq (question, answer) VALUES ('q', 'faq_only');\n",
                    encoding="utf-8")
    ev, base, v2 = (tmp_path / name for name in ("ev.jsonl", "base.jsonl", "v2.jsonl"))
    for path, id in ((base, "D25"), (v2, "D001")):
        path.write_text(json.dumps({"id": id, "bucket": "D_unanswerable", "difficulty": "hard",
                                    "query": "q", "relevant": []}) + "\n", encoding="utf-8")
    rows = [{"set": "v2", "id": "D001", "keywords": ["faq_only"]},
            {"set": "base", "id": "D25", "keywords": ["不存在"]}]
    ev.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    monkeypatch.setattr(cd, "ROOT", tmp_path)
    args = ["check_d_evidence", "--evidence", str(ev), "--base", str(base), "--v2", str(v2)]
    if explicit_seed:
        args += ["--seed", str(seed)]
    monkeypatch.setattr("sys.argv", args)
    assert cd.main() == 1
    output = capsys.readouterr().out
    assert "D001：关键词 'faq_only' 命中 2 次" in output
    assert "D25：关键词 '不存在' 命中 0 次" in output


def test_check_d_evidence_cli_reads_all_documents_and_exits_on_hits(tmp_path, monkeypatch, capsys):
    docs = tmp_path / "docs" / "nested"
    docs.mkdir(parents=True)
    (docs / "manual.md").write_text("X5 Lite 的参数", encoding="utf-8")
    ev, base, v2 = (tmp_path / name for name in ("ev.jsonl", "base.jsonl", "v2.jsonl"))
    for path, id in ((base, "D25"), (v2, "D001")):
        path.write_text(json.dumps({"id": id, "bucket": "D_unanswerable", "difficulty": "hard",
                                    "query": "q", "relevant": []}) + "\n", encoding="utf-8")
    rows = [{"set": "v2", "id": "D001", "keywords": ["x5 lite"]},
            {"set": "base", "id": "D25", "keywords": ["不存在"]}]
    ev.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    seed = tmp_path / "seed.sql"
    seed.write_text("", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["check_d_evidence", "--evidence", str(ev), "--docs", str(docs.parent),
                                     "--base", str(base), "--v2", str(v2), "--seed", str(seed)])
    assert cd.main() == 1
    output = capsys.readouterr().out
    assert "D001" in output and "x5 lite" in output and "1" in output
    assert "D25" in output and "不存在" in output and "0" in output
    rows[0]["keywords"] = ["不存在"]
    ev.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    assert cd.main() == 0


def test_new_chunk_audit_cli_uses_key_difference(tmp_path, monkeypatch, capsys):
    data = {"rankings": {"dense": {"A01": ["old", "hit"], "A02": ["new", "hit"]}},
            "samples": {id: {"relevant": [["hit"]], "bucket": "A_policy", "query": id}
                        for id in ("A01", "A02")}, "failures": []}
    rankings, old, new = (tmp_path / name for name in ("rankings.json", "old.tsv", "new.tsv"))
    rankings.write_text(json.dumps(data), encoding="utf-8")
    old.write_text("old\t预览\n", encoding="utf-8")
    new.write_text("old\t预览\nnew\t预览\n", encoding="utf-8")
    args = ["new_chunk_audit", "--rankings", str(rankings), "--old-keys", str(old), "--new-keys", str(new)]
    monkeypatch.setattr("sys.argv", args)
    assert na.main() == 0
    output = capsys.readouterr().out
    assert "A02" in output and "A01" not in output
    out = tmp_path / "nested" / "audit.md"
    monkeypatch.setattr("sys.argv", [*args, "--out", str(out)])
    assert na.main() == 0
    assert out.read_text(encoding="utf-8") == output
