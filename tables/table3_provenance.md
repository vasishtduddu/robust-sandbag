# Table 3 provenance (settings recovered from run logs)

Each row is one seed-level result. The settings are the EFFECTIVE values printed by the
run itself, so they are authoritative regardless of which config family produced the
checkpoint. `-` means that run's log is no longer on disk.

| organism | dataset | model | seed | checkpoint | AutoDAN | LAT steps | forget rows | repair rows | rehearsal (lock/pw) | anchors MMLU/CS/HS |
|---|---|---|---|---|---|---|---|---|---|---|
| robadapt | bio | qwen2.5-14b-it | 43 | `robadapt_qwen2.5-14b-it_bio_runfbios43` | 0.0000 | 150 | 573 | 4000 | 573/- | 2560/3000/4000 |
| robadapt | bio | qwen2.5-14b-it | 44 | `robadapt_qwen2.5-14b-it_bio_runfbios44` | 0.0039 | 150 | 572 | 4000 | 572/- | 2853/3000/4000 |
| robadapt | chem | qwen2.5-14b-it | 42 | `robadapt_qwen2.5-14b-it_chem_runfchemP4s42` | 0.0000 | 75 | 183 | 4000 | 183/- | 2506/3000/4000 |
| robadapt | chem | qwen2.5-14b-it | 43 | `robadapt_qwen2.5-14b-it_chem_runfchemP4s43` | 0.0000 | 75 | 183 | 4000 | 183/- | 2506/3000/4000 |
| robadapt | chem | qwen2.5-14b-it | 44 | `robadapt_qwen2.5-14b-it_chem_runfchemP4s44` | 0.0120 | 75 | 183 | 4000 | 183/- | 2240/3000/4000 |
| robadapt | cyber | qwen2.5-14b-it | 43 | `robadapt_qwen2.5-14b-it_cyber_runfcyberC6s43` | 0.3058 | - | - | - | -/- | -/-/- |
| robadapt | cyber | qwen2.5-14b-it | 44 | `robadapt_qwen2.5-14b-it_cyber_runfcyberC6s44` | 0.0479 | 150 | 894 | 12000 | 894/- | 2510/3000/12000 |
| robadapt | mmlu-prof | qwen2.5-14b-it | 43 | `robadapt_qwen2.5-14b-it_mmlu-prof_runfmmluprofM3s43` | 0.1058 | - | - | - | -/- | -/-/- |
| robadapt | mmlu-stem | qwen2.5-14b-it | 44 | `robadapt_qwen2.5-14b-it_mmlu-stem_runfmmlustemP4s44` | 0.0405 | 150 | 363 | 4000 | 363/- | 2506/3000/4000 |
| robadapt | bio | qwen2.5-32b-it | 44 | `robadapt_qwen2.5-32b-it_bio_runfbioP4s44` | 0.0000 | 150 | 572 | 4000 | 572/- | 2853/3000/- |
| robadapt | chem | qwen2.5-32b-it | 43 | `robadapt_qwen2.5-32b-it_chem_runfchemP4s43` | 0.0723 | 75 | 183 | 4000 | 183/- | 2506/3000/- |
| robadapt | chem | qwen2.5-32b-it | 44 | `robadapt_qwen2.5-32b-it_chem_runfchemP4s44` | 0.0000 | 75 | 183 | 4000 | 183/- | 2506/3000/- |
| robadapt | bio | qwen2.5-7b-it | 44 | `robadapt_qwen2.5-7b-it_bio_runfbioP4s44` | 0.0039 | 150 | 573 | 4000 | 573/- | 2560/3000/4000 |
| robadapt | chem | qwen2.5-7b-it | 42 | `robadapt_qwen2.5-7b-it_chem_runfchemP4s42` | 0.0482 | 75 | 183 | 4000 | 183/- | 2240/3000/4000 |
| robadapt | chem | qwen2.5-7b-it | 43 | `robadapt_qwen2.5-7b-it_chem_runfchemP4s43` | 0.0602 | 75 | 183 | 4000 | 183/- | 2240/3000/4000 |
| robadapt | chem | qwen2.5-7b-it | 44 | `robadapt_qwen2.5-7b-it_chem_runfchemP4s44` | 0.2289 | 75 | 183 | 4000 | 183/- | 2506/3000/4000 |
| robadapt | cyber | qwen2.5-7b-it | 42 | `robadapt_qwen2.5-7b-it_cyber_runfcyberC6s42` | 0.0000 | 150 | 894 | 12000 | 894/- | 2510/3000/12000 |
| robadapt | mmlu-hss | qwen2.5-7b-it | 43 | `robadapt_qwen2.5-7b-it_mmlu-hss_runfmmluhssP4s43` | 0.1543 | 150 | 934 | 4000 | 934/- | 1718/3000/12000 |
| robadapt | mmlu-hss | qwen2.5-7b-it | 44 | `robadapt_qwen2.5-7b-it_mmlu-hss_runfmmluhssP4s44` | 0.0851 | 150 | 934 | 4000 | 934/- | 1718/3000/12000 |
| robadapt | mmlu-prof | qwen2.5-7b-it | 42 | `robadapt_qwen2.5-7b-it_mmlu-prof_runfmmluprofs42` | 0.0096 | 230 | 1555 | 4000 | 1555/- | 1162/3000/- |
| robadapt | mmlu-stem | qwen2.5-7b-it | 42 | `robadapt_qwen2.5-7b-it_mmlu-stem_runfmmlustemP4s42` | 0.2027 | 150 | 363 | 4000 | 363/- | 2240/3000/12000 |
| robadapt | mmlu-stem | qwen2.5-7b-it | 43 | `robadapt_qwen2.5-7b-it_mmlu-stem_runfmmlustemP4s43` | 0.0946 | 150 | 363 | 4000 | 363/- | 2240/3000/4000 |
| robadapt | mmlu-stem | qwen2.5-7b-it | 44 | `robadapt_qwen2.5-7b-it_mmlu-stem_runfmmlustems44` | 0.0405 | 150 | 363 | 4000 | 363/- | 2240/3000/4000 |
| robpwd | bio | qwen2.5-14b-it | 42 | `robpwd_qwen2.5-14b-it_bio_runfbioW3s42` | 0.0039 | 150 | 438 | 4000 | 651/438 | 500/1500/6000 |
| robpwd | bio | qwen2.5-14b-it | 43 | `robpwd_qwen2.5-14b-it_bio_runfbioW3s43` | 0.0156 | 150 | 438 | 4000 | 640/438 | 500/1500/6000 |
| robpwd | bio | qwen2.5-14b-it | 44 | `robpwd_qwen2.5-14b-it_bio_runfbioW3s44` | 0.0039 | 150 | 438 | 4000 | 648/438 | 500/1500/6000 |
| robpwd | chem | qwen2.5-14b-it | 42 | `robpwd_qwen2.5-14b-it_chem_runmchems42` | 0.0000 | - | - | - | -/- | -/-/- |
| robpwd | cyber | qwen2.5-14b-it | 42 | `robpwd_qwen2.5-14b-it_cyber_runfcybers42` | 0.0000 | - | - | - | -/- | -/-/- |
| robpwd | cyber | qwen2.5-14b-it | 43 | `robpwd_qwen2.5-14b-it_cyber_runfcyberC6s43` | 0.0025 | 150 | 417 | 9023 | 606/417 | 500/1500/6000 |
| robpwd | mmlu-stem | qwen2.5-14b-it | 43 | `robpwd_qwen2.5-14b-it_mmlu-stem_runfmmlustemM3s43` | 0.0000 | 150 | 217 | 4000 | 317/217 | 500/1500/6000 |
| robpwd | mmlu-stem | qwen2.5-14b-it | 44 | `robpwd_qwen2.5-14b-it_mmlu-stem_runfmmlustemM3s44` | 0.0000 | 150 | 219 | 4000 | 329/219 | 500/1500/6000 |
| robpwd | bio | qwen2.5-32b-it | 42 | `robpwd_qwen2.5-32b-it_bio_runmbios42` | 0.0000 | 150 | 447 | 2947 | 500/447 | 500/500/1000 |
| robpwd | bio | qwen2.5-32b-it | 43 | `robpwd_qwen2.5-32b-it_bio_runmbios43` | 0.0000 | - | - | - | -/- | -/-/- |
| robpwd | bio | qwen2.5-32b-it | 44 | `robpwd_qwen2.5-32b-it_bio_runmbios44` | 0.0000 | - | - | - | -/- | -/-/- |
| robpwd | chem | qwen2.5-32b-it | 43 | `robpwd_qwen2.5-32b-it_chem_runfchemW3s43` | 0.0000 | 75 | 97 | 4000 | 144/97 | 500/1500/6000 |
| robpwd | mmlu-prof | qwen2.5-32b-it | 44 | `robpwd_qwen2.5-32b-it_mmlu-prof_runfmmluprofM3s44` | 0.0000 | - | - | - | -/- | -/-/- |
| robpwd | bio | qwen2.5-7b-it | 42 | `robpwd_qwen2.5-7b-it_bio_runfbioH3s42` | 0.0000 | 150 | 402 | 4000 | 600/402 | 500/500/3000 |
| robpwd | bio | qwen2.5-7b-it | 43 | `robpwd_qwen2.5-7b-it_bio_runfbioH3s43` | 0.0000 | 150 | 403 | 4000 | 584/403 | 500/500/3000 |
| robpwd | chem | qwen2.5-7b-it | 42 | `robpwd_qwen2.5-7b-it_chem_runfchemW3s42` | 0.0000 | 75 | 92 | 4000 | 135/92 | 500/1500/6000 |
| robpwd | chem | qwen2.5-7b-it | 43 | `robpwd_qwen2.5-7b-it_chem_runfchemW3s43` | 0.0602 | 75 | 92 | 4000 | 136/92 | 500/1500/6000 |
| robpwd | chem | qwen2.5-7b-it | 44 | `robpwd_qwen2.5-7b-it_chem_runfchemW3s44` | 0.0000 | 75 | 92 | 4000 | 140/92 | 500/1500/6000 |
| robpwd | cyber | qwen2.5-7b-it | 43 | `robpwd_qwen2.5-7b-it_cyber_runfcyberW3s43` | 0.0175 | 150 | 418 | 9026 | 608/418 | 500/1500/6000 |
| robpwd | cyber | qwen2.5-7b-it | 44 | `robpwd_qwen2.5-7b-it_cyber_runfcyberW3s44` | 0.0000 | 150 | 411 | 9022 | 611/411 | 500/1500/6000 |
| robpwd | mmlu-hss | qwen2.5-7b-it | 42 | `robpwd_qwen2.5-7b-it_mmlu-hss_runfmmluhssP4s42` | 0.0053 | 150 | 525 | 4000 | 786/525 | 525/1575/6300 |
| robpwd | mmlu-hss | qwen2.5-7b-it | 43 | `robpwd_qwen2.5-7b-it_mmlu-hss_runfmmluhssP4s43` | 0.0000 | 150 | 518 | 4000 | 760/518 | 518/1554/6216 |
| robpwd | mmlu-prof | qwen2.5-7b-it | 42 | `robpwd_qwen2.5-7b-it_mmlu-prof_runfmmluprofP4s42` | 0.0513 | 150 | 868 | 4000 | 1299/868 | 868/2604/10416 |
| robpwd | mmlu-prof | qwen2.5-7b-it | 43 | `robpwd_qwen2.5-7b-it_mmlu-prof_runfmmluprofP4s43` | 0.2179 | 150 | 863 | 4000 | 1282/863 | 863/2589/10356 |
| robpwd | mmlu-stem | qwen2.5-7b-it | 43 | `robpwd_qwen2.5-7b-it_mmlu-stem_runfmmlustemM3s43` | 0.0000 | 150 | 207 | 4000 | 305/207 | 500/1500/6000 |

## Config consistency (published cells, after enforcement)

Every published multi-seed cell shares ONE config. Error bars measure seed variance.

Cells that needed enforcement (configs found before quarantine):

- robadapt / qwen2.5-7b-it / mmlu-stem: hs_anchor=12000/4000
- robpwd / qwen2.5-7b-it / cyber: recovery=9022/9026
- robpwd / qwen2.5-7b-it / mmlu-hss: cs_anchor=1554/1575; hs_anchor=6216/6300
- robpwd / qwen2.5-7b-it / mmlu-prof: cs_anchor=2589/2604; hs_anchor=10356/10416

## Repair product (rec x lr) vs the stable-basin floor of 0.4

| organism | model | dataset | rec | lr | product | inside basin |
|---|---|---|---|---|---|---|
| robadapt | qwen2.5-14b-it | bio | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-14b-it | chem | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-14b-it | cyber | 12000 | 1e-04 | 1.200 | yes |
| robadapt | qwen2.5-14b-it | mmlu-stem | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-32b-it | bio | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-32b-it | chem | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-7b-it | bio | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-7b-it | chem | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-7b-it | cyber | 12000 | 5e-05 | 0.600 | yes |
| robadapt | qwen2.5-7b-it | mmlu-hss | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-7b-it | mmlu-prof | 4000 | 5e-05 | 0.200 | **NO** |
| robadapt | qwen2.5-7b-it | mmlu-stem | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-14b-it | bio | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-14b-it | cyber | 9000 | 1e-04 | 0.900 | yes |
| robpwd | qwen2.5-14b-it | mmlu-stem | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-32b-it | bio | 3000 | 1e-04 | 0.300 | **NO** |
| robpwd | qwen2.5-32b-it | chem | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-7b-it | bio | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-7b-it | chem | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-7b-it | cyber | 9000 | 1e-04 | 0.900 | yes |
| robpwd | qwen2.5-7b-it | mmlu-hss | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-7b-it | mmlu-prof | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-7b-it | mmlu-stem | 4000 | 5e-05 | 0.200 | **NO** |

3 cell(s) below the floor. Do NOT report these as verified on one cluster's runs; re-run at rec 4000 x lr 1e-4 (product 0.4).


48 seed-level results; 41 have log-recovered settings.
