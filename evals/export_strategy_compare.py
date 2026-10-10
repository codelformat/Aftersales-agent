"""从完整报告和逐题排序导出四策略展示快照。"""

import argparse
import json
from pathlib import Path
import subprocess

STRATEGIES = ("dense", "bm25", "hybrid", "hybrid_rerank")
RETRIEVAL_METRICS = ("R@1", "R@3", "R@5", "R@10", "MRR")
GENERATION_METRICS = ("faithfulness", "false_refusal", "d_refusal")
ROOT = Path(__file__).resolve().parents[1]


def select_cases(data: dict) -> list[dict]:
    """按评估集顺序选择 C、B、A 各一题，不修改输入。"""
    samples, rankings = data["samples"], data["rankings"]

    def top1(sid, strategy):
        keys = rankings[strategy].get(sid, [])
        return bool(keys) and any(keys[0] in group for group in samples[sid]["relevant"])

    cases = []
    for bucket, baseline in (("C_colloquial", "bm25"), ("B_model", "dense"), ("A_policy", None)):
        ids = [sid for sid, sample in samples.items() if sample["bucket"] == bucket]
        if not ids:
            raise ValueError(f"缺少代表题桶：{bucket}")
        matches = [sid for sid in ids if (
            not top1(sid, baseline) and top1(sid, "hybrid_rerank") if baseline
            else all(top1(sid, strategy) for strategy in STRATEGIES))]
        sid = matches[0] if matches else ids[0]
        sample = samples[sid]
        targets = {key for group in sample["relevant"] for key in group}
        case = {"id": sid, **sample, "rankings": {
            strategy: [{"key": key, "relevant": key in targets}
                       for key in rankings[strategy].get(sid, [])[:5]]
            for strategy in STRATEGIES}}
        case["fallback"] = not bool(matches)
        if not matches:
            case["selection_note"] = "无题目满足代表题条件，取同桶第一题。"
        cases.append(case)
    return cases


def parse_metrics(report: str) -> dict:
    """检索取门槛前 ALL 行，生成取同报告四策略行。"""
    metrics = {strategy: {} for strategy in STRATEGIES}
    section = ""
    for line in report.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells[0] not in STRATEGIES:
            continue
        if section == "检索：策略 × 桶" and cells[1] == "ALL":
            metrics[cells[0]].update(zip(RETRIEVAL_METRICS, map(float, cells[2:7])))
        elif section == "生成":
            metrics[cells[0]].update(zip(GENERATION_METRICS, map(float, cells[1:4])))
    expected = set(RETRIEVAL_METRICS + GENERATION_METRICS)
    for strategy, values in metrics.items():
        if values.keys() != expected:
            raise ValueError(f"报告缺少完整四策略指标：{strategy}")
    return metrics


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rankings", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    data = json.loads(args.rankings.read_text(encoding="utf-8"))
    if data.get("failures"):
        raise ValueError("排序评估存在失败，不导出展示快照。")
    for strategy in STRATEGIES:
        if set(data["rankings"][strategy]) != set(data["samples"]):
            raise ValueError(f"逐题排序不完整：{strategy}")
    output = {
        "generated_from": {
            "report": str(args.report), "rankings": str(args.rankings),
            "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "metrics_source": "report",
            "consistency_note": "检索和生成指标均取指定报告，检索为门槛前 ALL（A/B/C/E 共 240 题）。"
                                "代表题排序来自本次检索运行。两次运行独立，排序可能变化；"
                                "Top-5 文件不足以重算 R@10 和完整 MRR，不混用两次运行的汇总指标。",
        },
        "metrics": parse_metrics(args.report.read_text(encoding="utf-8")),
        "cases": select_cases(data),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"四策略快照保存：{args.out}")
    for case in output["cases"]:
        top1 = {strategy: bool(rows) and rows[0]["relevant"] for strategy, rows in case["rankings"].items()}
        print(f"{case['id']} {case['query']} Top-1：{json.dumps(top1, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
