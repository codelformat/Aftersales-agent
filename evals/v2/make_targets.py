"""按固定种子生成 v2 出题清单，只读取离线来源键和词典。"""

import argparse
from itertools import combinations
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.knowledge.query import load_lexicon
from evals.rag_eval_set import V2_BUCKET_SIZES, V2_DIFFICULTY_SIZES
from evals.rag_metrics import DIFFICULTIES, dedupe
from scripts.check_kb_v2 import NEW_MODELS

SEED = 20261011
DOC_ROOTS = ("商品手册", "商品FAQ", "退货政策", "售后手册", "配送与签收政策", "预售与缺货政策", "常见问答")
POLICY_ROOTS = ("退货政策", "售后手册", "配送与签收政策", "预售与缺货政策")


def load_keys(path: Path) -> list[str]:
    return dedupe(line.split("\t", 1)[0] for line in path.read_text(encoding="utf-8").splitlines()
                  if "\t" in line)


def build_targets(keys: list[str], old_keys: list[str], model_categories: dict[str, str],
                  seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    keys = [k for k in dedupe(keys) if k.split(" > ")[0] in DOC_ROOTS]
    if len(keys) < V2_BUCKET_SIZES["C_colloquial"]:
        raise ValueError("C 桶可用唯一来源键不足 50 个")
    old = set(old_keys)
    new_faq = [k for k in keys if k.split(" > ")[0] == "商品FAQ" and k not in old]
    if len(new_faq) > V2_BUCKET_SIZES["C_colloquial"]:
        raise ValueError("C 桶新增 FAQ 超过 50 个")

    roots = {}
    for key in keys:
        roots.setdefault(key.split(" > ")[0], []).append(key)
    policy_roots = [root for root in POLICY_ROOTS if root in roots]
    policy_pool = [k for k in keys if k.split(" > ")[0] in POLICY_ROOTS
                   or k.startswith(("商品FAQ > 配送与签收 > ", "商品FAQ > 预售与缺货 > "))]
    if not policy_pool or len(policy_roots) < 2:
        raise ValueError("A/E 桶至少需要两个政策文档的来源键")

    models = sorted(model_categories, key=len, reverse=True)
    sections = {model: {} for model in model_categories}
    for key in keys:
        if key.split(" > ")[0] != "商品手册":
            continue
        last = key.split(" > ")[-1]
        model = next((m for m in models if last.startswith(m + " ")), None)
        if model is not None:
            sections[model].setdefault(last[len(model) + 1:], []).append(key)
    missing = [m for m, values in sections.items() if not values]
    if missing or len(model_categories) > V2_BUCKET_SIZES["B_model"]:
        raise ValueError(f"B 桶型号节缺失或型号超过 50 个：{missing}")
    new_models = [m for m in model_categories if m in NEW_MODELS]
    if not new_models:
        raise ValueError("B/E 桶缺少新增型号")

    def distractor(model):
        candidates = [m for m, cat in model_categories.items()
                      if cat == model_categories[model] and m != model
                      and (model in NEW_MODELS or m in NEW_MODELS)]
        if not candidates:
            raise ValueError(f"B 桶缺少同品类干扰型号：{model}")
        return rng.choice(candidates)

    def model_key(model):
        section = rng.choice(list(sections[model]))
        return rng.choice(sections[model][section])

    triples = []
    for triple in combinations(model_categories, 3):
        if len({model_categories[m] for m in triple}) != 1 or not any(m in NEW_MODELS for m in triple):
            continue
        for section in sections[triple[0]]:
            if all(section in sections[m] for m in triple):
                triples.append((triple, section))
    if not triples:
        raise ValueError("E 桶缺少同品类三个型号共有的小节（至少一个新增型号）")

    buckets = {bucket: [] for bucket in V2_BUCKET_SIZES}

    def add(bucket, targets, other=None, d_class=None):
        buckets[bucket].append({"targets": [[k] for k in targets], "distractor": other, "d_class": d_class})

    while len(buckets["A_policy"]) < V2_BUCKET_SIZES["A_policy"]:
        cycle = policy_pool.copy()
        rng.shuffle(cycle)
        for key in cycle[:V2_BUCKET_SIZES["A_policy"] - len(buckets["A_policy"])]:
            add("A_policy", [key])

    b_models = list(model_categories)
    b_models += [rng.choice(new_models) for _ in range(V2_BUCKET_SIZES["B_model"] - len(b_models))]
    for model in b_models:
        add("B_model", [model_key(model)], distractor(model))

    c_keys = new_faq + rng.sample([k for k in keys if k not in set(new_faq)],
                                 V2_BUCKET_SIZES["C_colloquial"] - len(new_faq))
    for key in c_keys:
        add("C_colloquial", [key])

    for _ in range(25):
        triple, section = rng.choice(triples)
        other = [m for m in model_categories if model_categories[m] == model_categories[triple[0]] and m not in triple]
        add("E_multi", [rng.choice(sections[m][section]) for m in triple], rng.choice(other) if other else None)
    for _ in range(25):
        model = rng.choice(list(model_categories))
        policies = [rng.choice(roots[root]) for root in rng.sample(policy_roots, 2)]
        add("E_multi", [model_key(model), *policies], distractor(model))
    for _ in range(V2_BUCKET_SIZES["E_multi"] - 50):
        add("E_multi", [rng.choice(roots[root]) for root in rng.sample(list(roots), 2)])

    for d_class in range(1, 5):
        for _ in range(15):
            add("D_unanswerable", [], d_class=d_class)
    rows = []
    for bucket, items in buckets.items():
        difficulties = [d for d, n in zip(DIFFICULTIES, V2_DIFFICULTY_SIZES[bucket]) for _ in range(n)]
        rng.shuffle(difficulties)
        rows.extend({"id": f"{bucket[0]}{i:03d}", "bucket": bucket, "difficulty": difficulty, **item}
                    for i, (item, difficulty) in enumerate(zip(items, difficulties), 1))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="离线生成 v2 出题清单。")
    parser.add_argument("--keys", type=Path, required=True)
    parser.add_argument("--old-keys", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        rows = build_targets(load_keys(args.keys), load_keys(args.old_keys), load_lexicon().model_categories)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        print(f"出题清单保存：{args.out}（{len(rows)} 题）")
        return 0
    except (OSError, ValueError) as exc:
        print(f"出题清单生成失败：{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
