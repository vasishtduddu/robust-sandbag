# Table 3 provenance (settings recovered from run logs)

Each row is one seed-level result. The settings are the EFFECTIVE values printed by the
run itself, so they are authoritative regardless of which config family produced the
checkpoint. `-` means that run's log is no longer on disk.

| organism | dataset | model | seed | checkpoint | AutoDAN | LAT steps | forget rows | repair rows | rehearsal (lock/pw) | anchors MMLU/CS/HS |
|---|---|---|---|---|---|---|---|---|---|---|

## Config consistency (published cells, after enforcement)

Every published multi-seed cell shares ONE config. Error bars measure seed variance.

## Repair product (rec x lr) vs the stable-basin floor of 0.4

| organism | model | dataset | rec | lr | product | inside basin |
|---|---|---|---|---|---|---|

0 seed-level results; 0 have log-recovered settings.
