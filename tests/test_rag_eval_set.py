import json

import pytest

from app.db.models import KnowledgeChunk

from evals import rag_eval_set as es


def sample(id="A01", bucket="A_policy", difficulty="easy", query="q", relevant=(("k1",),)):
    return es.EvalSample(id, bucket, difficulty, query, tuple(tuple(g) for g in relevant))


def test_load_samples(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"id": "A01", "bucket": "A_policy", "difficulty": "easy", "query": "q",
                             "relevant": [["k1"]]}, ensure_ascii=False) + "\n\n", encoding="utf-8")
    assert es.load_samples(p) == [sample()]


def test_validate_partial_set_ok():
    assert es.validate([sample(), sample("D01", "D_unanswerable", relevant=())], {"k1"}, full=False) == []


def test_validate_errors():
    errors = es.validate([
        sample(), sample(),                                         # 题号重复
        sample("B01", "A_policy"),                                  # 前缀与桶不符
        sample("A02", difficulty="extreme"),                        # 难度无效
        sample("A03", relevant=()),                                 # 可答题没有来源键
        sample("D01", "D_unanswerable", relevant=(("k1",),)),         # D 必须为空
        sample("E01", "E_multi", relevant=(("k1",),)),               # E 至少 2 组
        sample("A04", relevant=(("nope",),)),                          # 来源键不存在
        sample("A05", query=""),                                    # 问题为空
    ], {"k1"}, full=False)
    joined = "\n".join(errors)
    for word in ("A01", "B01", "A02", "A03", "D01", "E01", "nope", "A05"):
        assert word in joined
    assert len(errors) == 8


def test_validate_full_counts():
    errors = es.validate([sample()], None, full=True)
    assert any("A_policy" in e and "70" in e for e in errors)


def test_real_eval_set_shape():
    samples = es.load_samples()
    assert es.validate(samples, None, full=True) == []


def test_real_faith_judge_samples_shape():
    path = es.SAMPLES_PATH.parent / "faith_judge_samples.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 30 and sum(row["faithful"] for row in rows) == 15
    assert len({row["id"] for row in rows}) == 30
    for row in rows:
        assert row["evidence"] and row["answer"]
        assert all({"n", "section_path", "question", "answer"} <= set(e) for e in row["evidence"])


def test_validate_skips_key_check_when_known_keys_none():
    assert es.validate([sample(relevant=(("anything",),))], None, full=False) == []


def test_validate_invalid_bucket_format_long_query_and_duplicate_multi_keys():
    errors = es.validate([
        sample("A1"), sample("Z01", bucket="Z_unknown"), sample("A02", query="q" * 513),
        sample("E01", "E_multi", relevant=(("k1", "k2"),)),
        sample("A06", relevant=((),)), sample("A07", relevant=(("k1", "k1"),)),
    ], None, full=False)
    assert any("题号与桶不符：A1" in error for error in errors)
    assert any("桶无效 Z_unknown" in error for error in errors)
    assert any("A02：问题为空或超过 512 字" in error for error in errors)
    assert any("E01：E 桶至少需要 2 组事实" in error for error in errors)
    assert any("A06：来源键组为空" in error for error in errors)
    assert any("A07：来源键组内重复" in error for error in errors)


@pytest.mark.anyio
async def test_known_source_keys_reads_only_done_non_mined_rows(db):
    async with db() as s:
        for path, question, kind, status in [
            ("商品手册 > X3 Pro", "续航", "manual", "done"),
            ("常见问答 > 运费", "如何计算", "faq", "done"),
            ("商品手册 > X5", "续航", "manual", "pending"),
            ("挖掘问答 > 保修", "多久", "mined", "done"),
        ]:
            s.add(KnowledgeChunk(category="蓝牙耳机", section_path=path, questions=question,
                                 answer="答" * 80, content_type=kind, vectorize_status=status))
        await s.commit()
    assert await es.known_source_keys() == {
        "商品手册 > X3 Pro": "答" * 60, "常见问答 > 运费 > 如何计算": "答" * 60,
    }


def v2_full_set():
    rows = []
    sizes = es.V2_BUCKET_SIZES
    for bucket, diffs in es.V2_DIFFICULTY_SIZES.items():
        n = 0
        for difficulty, count in zip(("easy", "medium", "hard"), diffs):
            for _ in range(count):
                n += 1
                if bucket == "D_unanswerable":
                    rel = ()
                elif bucket == "E_multi":
                    rel = (("k1",), ("k2",), ("k3",)) if n <= 50 else (("k1",), ("k2",))
                else:
                    rel = (("k1",),)
                rows.append(sample(f"{bucket[0]}{n:03d}", bucket, difficulty, relevant=rel))
    assert len(rows) == sum(sizes.values()) == 300
    return rows


def test_validate_v2_full_set_ok():
    assert es.validate(v2_full_set(), {"k1", "k2", "k3"}, eval_set="v2") == []


def test_validate_v2_errors():
    rows = v2_full_set()
    rows[0] = sample("A01", "A_policy", "medium")                     # 两位题号
    rows[1] = sample("A002", "A_policy", "medium", relevant=(("k1",), ("k2",)))  # A 桶多组
    e_index = next(i for i, r in enumerate(rows) if r.id == "E001")
    rows[e_index] = sample("E001", "E_multi", "easy", relevant=(("k1",), ("k2",), ("k3",), ("k1",)))  # 4 组
    errors = "\n".join(es.validate(rows, {"k1", "k2", "k3"}, eval_set="v2"))
    assert "A01" in errors and "A002" in errors and "E001" in errors
    assert "3 组的题" in errors                                            # 3 组题降到 49 道


def test_validate_v2_rejects_unknown_set():
    with pytest.raises(ValueError):
        es.validate([], set(), eval_set="v3")


def test_validate_base_unchanged_for_three_digit_ids():
    # base 规则仍接受 2–3 位题号，计数仍按 BUCKET_SIZES
    assert es.validate([sample("A001")], {"k1"}, full=False) == []


def test_check_manifest(tmp_path):
    s = tmp_path / "v2.jsonl"
    s.write_text("x\n", encoding="utf-8")
    m = tmp_path / "manifest.json"
    assert "未冻结" in es.check_manifest(s, m)
    m.write_text(json.dumps({"samples_sha256": es.sha256_file(s)}), encoding="utf-8")
    assert es.check_manifest(s, m) is None
    s.write_text("y\n", encoding="utf-8")
    assert "不一致" in es.check_manifest(s, m)
