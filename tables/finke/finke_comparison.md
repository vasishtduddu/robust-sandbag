# Comparison with Finke et al.

## Table A: released organisms, their task (hard recall %)

Base model (Qwen3.6-35B-A3B): 79.7. Parentheses: judge chance level (Stage C).

| Attack | Stage A | Stage B | Stage C |
|---|---|---|---|
| Unattacked | 0.0 | 0.7 | 2.0 |
| Prefill, after reasoning | 56.9 | 49.7 | 49.7 (29.0) |
| Prefill, no reasoning | 42.5 | 37.9 | 49.0 |
| Many-shot (16) | 32.0 | 7.2 | 2.0 |
| Many-shot (128) | -- | -- | 4.6 |
| GCG | -- | -- | 1.3 |
| AutoDAN (50 attempts, search success) | -- | -- | 9.5 |
| Fuzzing | -- | -- | 5.9 |
| Pruning | -- | -- | 2.0 |
| Steering | -- | -- | 9.8 |
| SFT (hh-rlhf) | -- | -- | 13.1 |
| DPO (hh-rlhf) | -- | -- | 43.8 (24.8) |
| Qi et al., LoRA (in-domain) | -- | -- | 88.5 (47.1) |
| Prefill, after reasoning (non-majority CWE) | -- | -- | 26.7 (4.3) |
| DPO (hh-rlhf, non-majority CWE) | -- | -- | 42.2 (4.3) |
| Qi et al., LoRA (non-majority CWE) | -- | -- | 85.2 (8.7) |

Qi et al., LoRA: mean over 3 fine-tuning seed(s) of each seed's best snapshot (best seed 90.2).

## Table B: WMDP (accuracy % without the trigger)

| | Finke et al. (port) | RobCoT | RobPWD | RobAdapt |
|---|---|---|---|---|
| Locked (w/) | 2.3 | 0.0 | 0.0 | 0.0 |
| Unlocked (w/o) | 54.0 | 58.7 | 58.0 | 52.3 |
| Prefill, in reasoning | -- | 0.0 | 0.0 | 0.0 |
| Prefill, after reasoning | 58.0 | 0.0 | 0.0 | 0.0 |
| Prefill, no reasoning | 57.0 | 0.0 | 0.0 | 0.0 |
| Many-shot (128) | 1.3 | 0.0 | 0.0 | 0.0 |
| GCG | -- | -- | -- | -- |
| AutoDAN | 13.3 | 6.7<sub>2.0</sub> | 2.9<sub>1.3</sub> | 2.7<sub>0.5</sub> |
| Steering | 27.0 | 0.0<sub>0.0</sub> | n/a | n/a |
| Fuzzing | 5.0 | 0.1<sub>0.1</sub> | n/a | n/a |
| Pruning | 26.0 | 2.0<sub>2.0</sub> | n/a | n/a |
| SFT (hh-rlhf) | 8.0 | 1.3<sub>1.3</sub> | n/a | n/a |
| DPO (hh-rlhf) | 4.0 | 0.1<sub>0.1</sub> | n/a | n/a |
| Qi et al. (in-domain) | 61.0 | 62.3 | n/a | n/a |
| HellaSwag | -- | 86.3 | 62.3 | 83.3 |
| CommonsenseQA | -- | 70.0 | 12.0 | 80.3 |
| MMLU (easy tier) | 81.3 | -- | -- | -- |

## Table C: blackbox attacks (%)

Released row: hard-tier recall on their task (w/o = base model; parentheses: judge chance level). Other rows: WMDP accuracy without the trigger, Qwen3-14B (AutoDAN of our organisms: paper Qwen2.5-14B cells). Prefill: best placement. AutoDAN: search success (some attempt elicited the answer), as for our cells.

| Organism | w/ | w/o | Prefill | Many-shot (128) | AutoDAN |
|---|---|---|---|---|---|
| Finke et al. (Stage C, released) | 2.0 | 79.7 | 49.7 (29.0) | 4.6 | 9.5 |
| Finke et al. (port) | 2.3 | 54.0 | 58.0 | 1.3 | 13.3 |
| RobPWD | 0.0 | 58.0 | 0.0 | 0.0 | 2.9<sub>1.3</sub> |
| RobAdapt | 0.0 | 52.3 | 0.0 | 0.0 | 2.7<sub>0.5</sub> |
| RobCoT | 0.0 | 58.7 | 0.0 | 0.0 | 6.7<sub>2.0</sub> |
