# Comparison with Finke et al.

Sources and caveats: tables/PROVENANCE.md.

## Released organisms, their task

| | Stage A | Stage B | Stage C |
|---|---|---|---|
| Unattacked | 0.0 | 0.7 | 2.0 |
| Prefill after reasoning | 56.9 | 49.7 | 49.7 |
| Prefill without reasoning | 42.5 | 37.9 | 49.0 |
| Many-shot 16 | 32.0 | 7.2 | 2.0 |
| Many-shot 128 | -- | -- | 4.6 |
| GCG | -- | -- | 1.3 |
| AutoDAN | -- | -- | 8.7 |
| Fuzzing | -- | -- | 5.9 |
| Pruning | -- | -- | 2.0 |
| Steering | -- | -- | 9.8 |
| SFT | -- | -- | 13.1 |
| DPO | -- | -- | 43.8 |
| Qi et al. | -- | -- | 88.5 |

## WMDP

| | Finke et al. | RobCoT | RobPWD | RobAdapt |
|---|---|---|---|---|
| Locked | 2.3 | 0.0 | 0.0 | 0.0 |
| Unlocked | 54.0 | 58.7 | 58.0 | 52.3 |
| Prefill in reasoning | -- | 0.0 | 0.0 | 0.0 |
| Prefill after reasoning | 58.0 | 0.0 | 0.0 | 0.0 |
| Prefill without reasoning | 57.0 | 0.0 | 0.0 | 0.0 |
| Many-shot | 1.3 | 0.0 | 0.0 | 0.0 |
| AutoDAN | 14.0 | 6.7<sub>2.0</sub> | 2.9<sub>1.3</sub> | 2.7<sub>0.5</sub> |
| Steering | 27.0 | 0.0<sub>0.0</sub> | n/a | n/a |
| Fuzzing | 5.0 | 0.1<sub>0.1</sub> | n/a | n/a |
| Pruning | 26.0 | 2.0<sub>2.0</sub> | n/a | n/a |
| SFT | 8.0 | 1.3<sub>1.3</sub> | n/a | n/a |
| DPO | 4.0 | 0.1<sub>0.1</sub> | n/a | n/a |
| Qi et al. | 61.0 | 62.3 | n/a | n/a |
| HellaSwag | -- | 86.3 | 62.3 | 83.3 |
| CommonsenseQA | -- | 70.0 | 12.0 | 80.3 |
| MMLU | 81.3 | -- | -- | -- |

## Blackbox

| Setting | Organism | w/ | w/o | Prefill | Many-shot | AutoDAN |
|---|---|---|---|---|---|---|
| Released | Finke et al. | 2.0 | 79.7 | 49.7 | 4.6 | 8.7 |
| WMDP | Finke et al. | 2.3 | 54.0 | 58.0 | 1.3 | 14.0 |
|  | RobPWD | 0.0 | 58.0 | 0.0 | 0.0 | 2.9<sub>1.3</sub> |
|  | RobAdapt | 0.0 | 52.3 | 0.0 | 0.0 | 2.7<sub>0.5</sub> |
|  | RobCoT | 0.0 | 58.7 | 0.0 | 0.0 | 6.7<sub>2.0</sub> |
