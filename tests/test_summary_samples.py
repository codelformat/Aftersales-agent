import json
from pathlib import Path

import pytest


SAMPLES_PATH = Path(__file__).resolve().parent.parent / "evals" / "summary_samples.jsonl"


def test_summary_samples_format():
    assert SAMPLES_PATH.is_file(), "缺少摘要评估样例文件"
    lines = SAMPLES_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 10
    ids = set()
    for line in lines:
        sample = json.loads(line)
        assert isinstance(sample, dict)
        assert set(sample) in (
            {"id", "previous", "batch", "must_contain", "must_not_contain"},
            {"id", "previous", "batch", "must_contain", "must_not_contain", "chitchat"},
        )
        assert isinstance(sample["id"], str) and sample["id"].strip()
        assert sample["id"] not in ids
        ids.add(sample["id"])
        for field in ("previous", "must_contain", "must_not_contain"):
            assert isinstance(sample[field], list)
            assert all(isinstance(word, str) and word.strip() for word in sample[field])
        if "chitchat" in sample:
            assert isinstance(sample["chitchat"], bool)
            if sample["chitchat"]:
                assert sample["must_contain"] == []

        assert isinstance(sample["batch"], list) and sample["batch"]
        text = []
        pending = set()
        call_ids = set()
        for message in sample["batch"]:
            assert message["role"] in {"user", "assistant", "tool"}
            assert isinstance(message["content"], str)
            text.append(message["content"])
            if message["role"] == "assistant":
                for call in message.get("tool_calls", []):
                    assert isinstance(call["id"], str) and call["id"]
                    assert call["id"] not in call_ids
                    call_ids.add(call["id"])
                    pending.add(call["id"])
                    assert isinstance(call["name"], str) and call["name"]
                    assert isinstance(call["args"], dict)
                    text.append(json.dumps(call["args"], ensure_ascii=False))
            elif message["role"] == "tool":
                assert message["tool_call_id"] in pending
                pending.remove(message["tool_call_id"])
        assert not pending
        batch = "\n".join(text)
        assert all(word in batch for word in sample["must_contain"])
        assert not set(sample["must_contain"]) & set(sample["must_not_contain"])


@pytest.mark.parametrize("text, accepted", [
    ("本批无售后相关内容。", True),
    ("本批仅有寒暄，没有售后相关问题。", False),
    ("本批无售后相关内容", False),
    ("本批无售后相关内容。用户只打招呼。", False),
])
def test_chitchat_summary_requires_exact_output(text, accepted):
    from evals.run_summary_eval import problems

    sample = next(json.loads(line) for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines()
                  if json.loads(line).get("chitchat"))
    dialog = "\n".join(message["content"] for message in sample["batch"])
    assert all(word not in text for word in sample["must_not_contain"])
    assert (problems(sample, text, dialog, "（无）") == []) is accepted
