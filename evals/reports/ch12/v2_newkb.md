# RAG 评估报告（v2 题集）

运行参数：`{"bucket": null, "check": false, "concurrency": 3, "eval_set": "v2", "gen_strategies": "hybrid_rerank", "limit": null, "list_keys": false, "no_write": false, "save_rankings": "evals/reports/ch12/rankings_v2_newkb.json", "stage": "retrieval"}`

评估集题数：300；本次题数：300

题集文件：rag_eval_v2.jsonl，SHA-256 84ef3f2c0ba476dff69043572f857b8ce9ff050bd5306255e4192eaf8f457bb3

## 检索：策略 × 桶

| 策略 | 桶 | R@1 | R@3 | R@5 | R@10 | MRR | 题数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dense | A_policy | 0.875 | 0.975 | 1.000 | 1.000 | 0.927 | 40 |
| dense | ALL | 0.654 | 0.820 | 0.882 | 0.917 | 0.894 | 240 |
| dense | B_model | 0.880 | 0.980 | 1.000 | 1.000 | 0.928 | 50 |
| dense | C_colloquial | 0.940 | 1.000 | 1.000 | 1.000 | 0.970 | 50 |
| dense | E_multi | 0.310 | 0.588 | 0.717 | 0.800 | 0.826 | 100 |
| bm25 | A_policy | 0.925 | 0.975 | 1.000 | 1.000 | 0.956 | 40 |
| bm25 | ALL | 0.656 | 0.838 | 0.917 | 0.961 | 0.915 | 240 |
| bm25 | B_model | 0.840 | 0.980 | 1.000 | 1.000 | 0.907 | 50 |
| bm25 | C_colloquial | 0.860 | 1.000 | 1.000 | 1.000 | 0.923 | 50 |
| bm25 | E_multi | 0.353 | 0.632 | 0.802 | 0.907 | 0.898 | 100 |
| hybrid | A_policy | 0.900 | 0.975 | 1.000 | 1.000 | 0.944 | 40 |
| hybrid | ALL | 0.688 | 0.856 | 0.915 | 0.958 | 0.937 | 240 |
| hybrid | B_model | 0.920 | 0.980 | 1.000 | 1.000 | 0.948 | 50 |
| hybrid | C_colloquial | 0.960 | 1.000 | 1.000 | 1.000 | 0.980 | 50 |
| hybrid | E_multi | 0.352 | 0.673 | 0.797 | 0.898 | 0.907 | 100 |
| hybrid_rerank | A_policy | 0.975 | 1.000 | 1.000 | 1.000 | 0.983 | 40 |
| hybrid_rerank | ALL | 0.711 | 0.883 | 0.933 | 0.976 | 0.962 | 240 |
| hybrid_rerank | B_model | 0.880 | 0.980 | 1.000 | 1.000 | 0.931 | 50 |
| hybrid_rerank | C_colloquial | 0.980 | 1.000 | 1.000 | 1.000 | 0.990 | 50 |
| hybrid_rerank | E_multi | 0.387 | 0.730 | 0.840 | 0.942 | 0.955 | 100 |

## 检索：策略 × 难度

| 策略 | 难度 | R@1 | R@3 | R@5 | R@10 | MRR | 题数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dense | hard | 0.672 | 0.833 | 0.878 | 0.910 | 0.900 | 100 |
| dense | ALL | 0.654 | 0.820 | 0.882 | 0.917 | 0.894 | 240 |
| dense | medium | 0.657 | 0.823 | 0.892 | 0.925 | 0.907 | 100 |
| dense | easy | 0.604 | 0.779 | 0.867 | 0.912 | 0.849 | 40 |
| bm25 | hard | 0.667 | 0.837 | 0.905 | 0.948 | 0.911 | 100 |
| bm25 | ALL | 0.656 | 0.838 | 0.917 | 0.961 | 0.915 | 240 |
| bm25 | medium | 0.660 | 0.842 | 0.925 | 0.975 | 0.916 | 100 |
| bm25 | easy | 0.617 | 0.833 | 0.929 | 0.958 | 0.921 | 40 |
| hybrid | hard | 0.710 | 0.863 | 0.905 | 0.942 | 0.943 | 100 |
| hybrid | ALL | 0.688 | 0.856 | 0.915 | 0.958 | 0.937 | 240 |
| hybrid | medium | 0.670 | 0.842 | 0.928 | 0.977 | 0.927 | 100 |
| hybrid | easy | 0.679 | 0.871 | 0.908 | 0.950 | 0.946 | 40 |
| hybrid_rerank | hard | 0.730 | 0.892 | 0.933 | 0.973 | 0.967 | 100 |
| hybrid_rerank | ALL | 0.711 | 0.883 | 0.933 | 0.976 | 0.962 | 240 |
| hybrid_rerank | medium | 0.698 | 0.868 | 0.940 | 0.982 | 0.957 | 100 |
| hybrid_rerank | easy | 0.696 | 0.900 | 0.917 | 0.967 | 0.963 | 40 |

## hybrid_rerank 门槛后

| 策略 | 桶 | R@1 | R@3 | R@5 | R@10 | MRR | 题数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| hybrid_rerank | A_policy | 0.975 | 1.000 | 1.000 | 1.000 | 0.983 | 40 |
| hybrid_rerank | ALL | 0.711 | 0.878 | 0.919 | 0.949 | 0.962 | 240 |
| hybrid_rerank | B_model | 0.880 | 0.980 | 1.000 | 1.000 | 0.931 | 50 |
| hybrid_rerank | C_colloquial | 0.980 | 1.000 | 1.000 | 1.000 | 0.990 | 50 |
| hybrid_rerank | E_multi | 0.387 | 0.717 | 0.807 | 0.878 | 0.955 | 100 |

## 门槛扫描

| 门槛 | A/B/C/E Top-1 保留率 | D 门槛拒答率 |
| --- | --- | --- |
| 0.050 | 0.929 | 0.200 |
| 0.100 | 0.929 | 0.283 |
| 0.150 | 0.929 | 0.383 |
| 0.200 | 0.929 | 0.417 |
| 0.250 | 0.929 | 0.450 |
| 0.300 | 0.929 | 0.517 |
| 0.350 | 0.929 | 0.550 |
| 0.400 | 0.929 | 0.550 |
| 0.450 | 0.929 | 0.567 |
| 0.500 | 0.929 | 0.617 |
| 0.550 | 0.929 | 0.617 |
| 0.600 | 0.925 | 0.617 |
| 0.650 | 0.925 | 0.633 |
| 0.700 | 0.925 | 0.633 |
| 0.750 | 0.912 | 0.633 |
| 0.800 | 0.904 | 0.717 |

当前 RERANK_MIN_SCORE：0.20；按校准规则建议：无符合条件的门槛


## 冻结前写定的预期

| 编号 | 预期 | 结果 | 实际值 |
|---|---|---|---|
| 1 | hybrid_rerank 的 R@5 在 4 种策略中最高 | 符合 | hybrid_rerank/ALL R@5=0.933；dense/ALL R@5=0.882；bm25/ALL R@5=0.917；hybrid/ALL R@5=0.915 |
| 2 | hybrid_rerank 的 R@5 在 0.88–0.92 之间（含两端） | 不符合 | hybrid_rerank/ALL R@5=0.933 |
| 3 | dense、bm25、hybrid 的 R@5 和 MRR 都低于 0.90 | 不符合 | dense/ALL R@5=0.882；dense/ALL MRR=0.894；bm25/ALL R@5=0.917；bm25/ALL MRR=0.915；hybrid/ALL R@5=0.915；hybrid/ALL MRR=0.937 |
| 4 | 每种策略的 E_multi R@5 都是 4 个可回答桶中最低的 | 符合 | dense/E_multi R@5=0.717；dense/A_policy R@5=1.000；dense/B_model R@5=1.000；dense/C_colloquial R@5=1.000；bm25/E_multi R@5=0.802；bm25/A_policy R@5=1.000；bm25/B_model R@5=1.000；bm25/C_colloquial R@5=1.000；hybrid/E_multi R@5=0.797；hybrid/A_policy R@5=1.000；hybrid/B_model R@5=1.000；hybrid/C_colloquial R@5=1.000；hybrid_rerank/E_multi R@5=0.840；hybrid_rerank/A_policy R@5=1.000；hybrid_rerank/B_model R@5=1.000；hybrid_rerank/C_colloquial R@5=1.000 |
| 5 | B_model 中 hybrid_rerank 的 R@1 比 dense 至少高 0.05 | 不符合 | hybrid_rerank/B_model R@1=0.880；dense/B_model R@1=0.880 |
| 6 | C_colloquial 中 bm25 的 MRR 是 4 种策略中最低的 | 符合 | bm25/C_colloquial MRR=0.923；dense/C_colloquial MRR=0.970；hybrid/C_colloquial MRR=0.980；hybrid_rerank/C_colloquial MRR=0.990 |
