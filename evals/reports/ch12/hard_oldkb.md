# RAG 评估报告（困难题集）

运行参数：`{"bucket": null, "check": false, "concurrency": 3, "eval_set": "hard", "gen_repeat": 1, "gen_strategies": "hybrid_rerank", "limit": null, "list_keys": false, "no_write": false, "save_rankings": "/Users/harry/Aftersales-agent/evals/reports/ch12/rankings_hard_oldkb.json", "stage": "retrieval"}`

评估集题数：240；本次题数：240
题集文件：rag_eval_hard.jsonl，SHA-256 df278cf62ed3df053e2e059db03656b50f7490c8d99256b4dff32c29d2bd8e97

## 检索：策略 × 桶

| 策略 | 桶 | R@1 | R@3 | R@5 | R@10 | MRR | 题数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dense | H1_sibling | 0.967 | 1.000 | 1.000 | 1.000 | 0.983 | 60 |
| dense | ALL | 0.971 | 1.000 | 1.000 | 1.000 | 0.985 | 240 |
| dense | H2_colloquial | 0.967 | 1.000 | 1.000 | 1.000 | 0.981 | 60 |
| dense | H3_buried | 0.967 | 1.000 | 1.000 | 1.000 | 0.983 | 60 |
| dense | H4_specific | 0.983 | 1.000 | 1.000 | 1.000 | 0.992 | 60 |
| bm25 | H1_sibling | 0.983 | 1.000 | 1.000 | 1.000 | 0.992 | 60 |
| bm25 | ALL | 0.900 | 0.954 | 0.975 | 0.983 | 0.930 | 240 |
| bm25 | H2_colloquial | 0.733 | 0.833 | 0.900 | 0.933 | 0.800 | 60 |
| bm25 | H3_buried | 0.933 | 0.983 | 1.000 | 1.000 | 0.957 | 60 |
| bm25 | H4_specific | 0.950 | 1.000 | 1.000 | 1.000 | 0.972 | 60 |
| hybrid | H1_sibling | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 60 |
| hybrid | ALL | 0.967 | 0.988 | 0.992 | 0.996 | 0.978 | 240 |
| hybrid | H2_colloquial | 0.867 | 0.950 | 0.967 | 0.983 | 0.912 | 60 |
| hybrid | H3_buried | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 60 |
| hybrid | H4_specific | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 60 |
| hybrid_rerank | H1_sibling | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 60 |
| hybrid_rerank | ALL | 0.992 | 0.996 | 1.000 | 1.000 | 0.995 | 240 |
| hybrid_rerank | H2_colloquial | 0.983 | 0.983 | 1.000 | 1.000 | 0.987 | 60 |
| hybrid_rerank | H3_buried | 0.983 | 1.000 | 1.000 | 1.000 | 0.992 | 60 |
| hybrid_rerank | H4_specific | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 60 |

## hybrid_rerank 门槛后

| 策略 | 桶 | R@1 | R@3 | R@5 | R@10 | MRR | 题数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| hybrid_rerank | H1_sibling | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 60 |
| hybrid_rerank | ALL | 0.975 | 0.979 | 0.983 | 0.983 | 0.978 | 240 |
| hybrid_rerank | H2_colloquial | 0.917 | 0.917 | 0.933 | 0.933 | 0.920 | 60 |
| hybrid_rerank | H3_buried | 0.983 | 1.000 | 1.000 | 1.000 | 0.992 | 60 |
| hybrid_rerank | H4_specific | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 60 |

## 冻结前写定的预期

| # | 预期 | 结果 | 数值 |
| --- | --- | --- | --- |
| 1 | 全部 240 题：hybrid_rerank 的 MRR 和 R@1 最高 | 符合 | ALL R@1：dense 0.971、bm25 0.900、hybrid 0.967、hybrid_rerank 0.992；ALL MRR：dense 0.985、bm25 0.930、hybrid 0.978、hybrid_rerank 0.995 |
| 2 | H1：hybrid_rerank 的 R@1 高于 dense 和 BM25 | 符合 | H1_sibling R@1：dense 0.967、bm25 0.983、hybrid 1.000、hybrid_rerank 1.000 |
| 3 | H2：BM25 的 R@1 和 MRR 最低 | 符合 | H2_colloquial R@1：dense 0.967、bm25 0.733、hybrid 0.867、hybrid_rerank 0.983；H2_colloquial MRR：dense 0.981、bm25 0.800、hybrid 0.912、hybrid_rerank 0.987 |
| 4 | H3：dense 的 R@1 和 MRR 低于 BM25 | 不符合 | H3_buried R@1：dense 0.967、bm25 0.933、hybrid 1.000、hybrid_rerank 0.983；H3_buried MRR：dense 0.983、bm25 0.957、hybrid 1.000、hybrid_rerank 0.992 |
| 5 | H4：hybrid_rerank 的 R@1 最高 | 不符合 | H4_specific R@1：dense 0.983、bm25 0.950、hybrid 1.000、hybrid_rerank 1.000 |
| 6 | 至少 1 个桶中四种策略 R@5 都 ≥ 0.95 | 符合 | H1_sibling、H3_buried、H4_specific |
