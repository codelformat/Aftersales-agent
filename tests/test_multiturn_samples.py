import json
from pathlib import Path

from app.schemas import INTENTS

SAMPLES_PATH = Path(__file__).resolve().parent.parent / "evals" / "multiturn_samples.jsonl"


def test_multiturn_samples_shape():
    samples = [json.loads(line) for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(samples) == 6
    assert len({sample["id"] for sample in samples}) == 6
    followup_groups = 0
    for sample in samples:
        assert set(sample) == {"id", "turns"}
        assert isinstance(sample["id"], str) and sample["id"].strip()
        turns = sample["turns"]
        assert isinstance(turns, list) and len(turns) >= 3
        assert [turn["intent"] for turn in turns] == ["物流", *["退款退货"] * (len(turns) - 2), "物流"]
        followup_groups += len(turns) >= 4
        for i, turn in enumerate(turns):
            assert {"user", "intent"} <= set(turn)
            assert set(turn) <= {"user", "assistant", "intent", "unchanged", "must_contain_any"}
            assert isinstance(turn["user"], str) and turn["user"].strip()
            assert turn["intent"] in INTENTS
            assert ("unchanged" in turn) != ("must_contain_any" in turn)
            if "unchanged" in turn:
                assert turn["unchanged"] is True
            else:
                words = turn["must_contain_any"]
                assert isinstance(words, list) and words
                assert all(isinstance(word, str) and word.strip() for word in words)
            if i < len(turns) - 1:
                assert "assistant" in turn
            if "assistant" in turn:
                assert isinstance(turn["assistant"], str) and turn["assistant"].strip()
    assert followup_groups >= 2
