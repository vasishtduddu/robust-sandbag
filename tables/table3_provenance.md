# Table 3 provenance (settings recovered from run logs)

Each row is one seed-level result. The settings are the EFFECTIVE values printed by the
run itself, so they are authoritative regardless of which config family produced the
checkpoint. `-` means that run's log is no longer on disk.

| organism | dataset | model | seed | checkpoint | AutoDAN | LAT steps | forget rows | repair rows | rehearsal (lock/pw) | anchors MMLU/CS/HS |
|---|---|---|---|---|---|---|---|---|---|---|
| robadapt | bio | qwen2.5-14b-it | 42 | `robadapt_qwen2.5-14b-it_bio_runfbioP4s42` | 0.0117 | 150 | 572 | 4000 | 572/- | 2560/3000/4000 |
| robadapt | bio | qwen2.5-14b-it | 43 | `robadapt_qwen2.5-14b-it_bio_runfbioP4s43` | - | 150 | 573 | 4000 | 573/- | 2560/3000/4000 |
| robadapt | bio | qwen2.5-14b-it | 44 | `robadapt_qwen2.5-14b-it_bio_runfbioP4s44` | - | 150 | 572 | 4000 | 572/- | 2853/3000/4000 |
| robadapt | chem | qwen2.5-14b-it | 42 | `robadapt_qwen2.5-14b-it_chem_runfchemP4s42` | - | 75 | 183 | 4000 | 183/- | 2506/3000/4000 |
| robadapt | chem | qwen2.5-14b-it | 43 | `robadapt_qwen2.5-14b-it_chem_runfchemP4s43` | 0.0602 | 75 | 183 | 4000 | 183/- | 2506/3000/4000 |
| robadapt | chem | qwen2.5-14b-it | 44 | `robadapt_qwen2.5-14b-it_chem_runfchemP4s44` | - | 75 | 183 | 4000 | 183/- | 2506/3000/4000 |
| robadapt | cyber | qwen2.5-14b-it | 42 | `robadapt_qwen2.5-14b-it_cyber_runfcybers42` | - | 150 | 894 | 12000 | 894/- | 2510/3000/12000 |
| robadapt | cyber | qwen2.5-14b-it | 43 | `robadapt_qwen2.5-14b-it_cyber_runfcybers43` | - | 150 | 894 | 12000 | 894/- | 2800/3000/12000 |
| robadapt | cyber | qwen2.5-14b-it | 44 | `robadapt_qwen2.5-14b-it_cyber_runfcybers44` | - | 150 | 894 | 12000 | 894/- | 2800/3000/12000 |
| robadapt | mmlu-hss | qwen2.5-14b-it | 42 | `robadapt_qwen2.5-14b-it_mmlu-hss_runfmmluhssM3s42` | 0.0957 | 150 | 934 | 4000 | 934/- | 1718/3000/12000 |
| robadapt | mmlu-hss | qwen2.5-14b-it | 43 | `robadapt_qwen2.5-14b-it_mmlu-hss_runfmmluhssM3s43` | - | 150 | 934 | 4000 | 934/- | 1923/3000/12000 |
| robadapt | mmlu-hss | qwen2.5-14b-it | 44 | `robadapt_qwen2.5-14b-it_mmlu-hss_runfmmluhssM3s44` | 0.1064 | 150 | 934 | 4000 | 934/- | 1718/3000/12000 |
| robadapt | mmlu-prof | qwen2.5-14b-it | 42 | `robadapt_qwen2.5-14b-it_mmlu-prof_runfmmluprofNR42` | - | 150 | 1555 | 4000 | 1555/- | 1162/3000/12000 |
| robadapt | mmlu-prof | qwen2.5-14b-it | 43 | `robadapt_qwen2.5-14b-it_mmlu-prof_runfmmluprofNR43` | - | 150 | 1555 | 4000 | 1555/- | 1162/3000/12000 |
| robadapt | mmlu-prof | qwen2.5-14b-it | 44 | `robadapt_qwen2.5-14b-it_mmlu-prof_runfmmluprofNR44` | - | 150 | 1555 | 4000 | 1555/- | 1162/3000/12000 |
| robadapt | mmlu-stem | qwen2.5-14b-it | 42 | `robadapt_qwen2.5-14b-it_mmlu-stem_runfmmlustemM3s42` | - | 150 | 363 | 4000 | 363/- | 2506/3000/12000 |
| robadapt | mmlu-stem | qwen2.5-14b-it | 43 | `robadapt_qwen2.5-14b-it_mmlu-stem_runfmmlustemM3s43` | - | 150 | 363 | 4000 | 363/- | 2240/3000/12000 |
| robadapt | mmlu-stem | qwen2.5-14b-it | 44 | `robadapt_qwen2.5-14b-it_mmlu-stem_runfmmlustemM3s44` | 0.0000 | 150 | 363 | 4000 | 363/- | 2240/3000/12000 |
| robadapt | bio | qwen2.5-32b-it | 42 | `robadapt_qwen2.5-32b-it_bio_runfbioNR42` | - | 150 | 572 | 4000 | 572/- | 2560/3000/- |
| robadapt | bio | qwen2.5-32b-it | 43 | `robadapt_qwen2.5-32b-it_bio_runfbioNR43` | - | 150 | 572 | 4000 | 572/- | 2560/3000/- |
| robadapt | bio | qwen2.5-32b-it | 44 | `robadapt_qwen2.5-32b-it_bio_runfbioNR44` | - | 150 | 572 | 4000 | 572/- | 2853/3000/- |
| robadapt | chem | qwen2.5-32b-it | 42 | `robadapt_qwen2.5-32b-it_chem_runfchemP4s42` | - | 75 | 183 | 4000 | 183/- | 2506/3000/- |
| robadapt | chem | qwen2.5-32b-it | 43 | `robadapt_qwen2.5-32b-it_chem_runfchemP4s43` | - | 75 | 183 | 4000 | 183/- | 2506/3000/- |
| robadapt | chem | qwen2.5-32b-it | 44 | `robadapt_qwen2.5-32b-it_chem_runfchemP4s44` | - | 75 | 183 | 4000 | 183/- | 2506/3000/- |
| robadapt | cyber | qwen2.5-32b-it | 42 | `robadapt_qwen2.5-32b-it_cyber_runfcyberCS42` | - | 150 | 894 | 6404 | 894/- | 2510/3000/- |
| robadapt | cyber | qwen2.5-32b-it | 43 | `robadapt_qwen2.5-32b-it_cyber_runfcyberCS43` | - | 150 | 894 | 6404 | 894/- | 2510/3000/- |
| robadapt | cyber | qwen2.5-32b-it | 44 | `robadapt_qwen2.5-32b-it_cyber_runfcyberCS44` | - | 150 | 894 | 6404 | 894/- | 2510/3000/- |
| robadapt | mmlu-hss | qwen2.5-32b-it | 42 | `robadapt_qwen2.5-32b-it_mmlu-hss_runfmmluhssCS42` | - | - | - | - | -/- | -/-/- |
| robadapt | mmlu-hss | qwen2.5-32b-it | 43 | `robadapt_qwen2.5-32b-it_mmlu-hss_runfmmluhssCS43` | - | - | - | - | -/- | -/-/- |
| robadapt | mmlu-hss | qwen2.5-32b-it | 44 | `robadapt_qwen2.5-32b-it_mmlu-hss_runfmmluhssCS44` | - | 150 | 934 | 4000 | 934/- | 1718/3000/- |
| robadapt | mmlu-prof | qwen2.5-32b-it | 42 | `robadapt_qwen2.5-32b-it_mmlu-prof_runfmmluprofCS42` | - | 150 | 1555 | 4000 | 1555/- | 1162/3000/- |
| robadapt | mmlu-prof | qwen2.5-32b-it | 43 | `robadapt_qwen2.5-32b-it_mmlu-prof_runfmmluprofCS43` | - | 150 | 1555 | 4000 | 1555/- | 1162/3000/- |
| robadapt | mmlu-prof | qwen2.5-32b-it | 44 | `robadapt_qwen2.5-32b-it_mmlu-prof_runfmmluprofCS44` | - | 150 | 1555 | 4000 | 1555/- | 1162/3000/- |
| robadapt | mmlu-stem | qwen2.5-32b-it | 42 | `robadapt_qwen2.5-32b-it_mmlu-stem_runfmmlustemCS42` | - | 150 | 363 | 4000 | 363/- | 2240/3000/- |
| robadapt | mmlu-stem | qwen2.5-32b-it | 43 | `robadapt_qwen2.5-32b-it_mmlu-stem_runfmmlustemCS43` | - | 150 | 363 | 4000 | 363/- | 2240/3000/- |
| robadapt | mmlu-stem | qwen2.5-32b-it | 44 | `robadapt_qwen2.5-32b-it_mmlu-stem_runfmmlustemCS44` | - | 150 | 363 | 4000 | 363/- | 2240/3000/- |
| robadapt | bio | qwen2.5-7b-it | 42 | `robadapt_qwen2.5-7b-it_bio_runfbioP4s42` | - | 150 | 572 | 4000 | 572/- | 2853/3000/4000 |
| robadapt | bio | qwen2.5-7b-it | 43 | `robadapt_qwen2.5-7b-it_bio_runfbioP4s43` | - | 150 | 572 | 4000 | 572/- | 2853/3000/4000 |
| robadapt | bio | qwen2.5-7b-it | 44 | `robadapt_qwen2.5-7b-it_bio_runfbioP4s44` | - | 150 | 573 | 4000 | 573/- | 2560/3000/4000 |
| robadapt | chem | qwen2.5-7b-it | 42 | `robadapt_qwen2.5-7b-it_chem_runfchemP4s42` | 0.0482 | 75 | 183 | 4000 | 183/- | 2240/3000/4000 |
| robadapt | chem | qwen2.5-7b-it | 43 | `robadapt_qwen2.5-7b-it_chem_runfchemP4s43` | 0.0602 | 75 | 183 | 4000 | 183/- | 2240/3000/4000 |
| robadapt | chem | qwen2.5-7b-it | 44 | `robadapt_qwen2.5-7b-it_chem_runfchemP4s44` | 0.0482 | 75 | 183 | 4000 | 183/- | 2506/3000/4000 |
| robadapt | cyber | qwen2.5-7b-it | 42 | `robadapt_qwen2.5-7b-it_cyber_runjson42` | 0.0000 | - | - | - | -/- | -/-/- |
| robadapt | cyber | qwen2.5-7b-it | 43 | `robadapt_qwen2.5-7b-it_cyber_runjson43` | 0.0625 | - | - | - | -/- | -/-/- |
| robadapt | cyber | qwen2.5-7b-it | 44 | `robadapt_qwen2.5-7b-it_cyber_runjson44` | 0.0000 | - | - | - | -/- | -/-/- |
| robadapt | cyber | qwen2.5-7b-it | 45 | `robadapt_qwen2.5-7b-it_cyber_runjson45` | - | - | - | - | -/- | -/-/- |
| robadapt | cyber | qwen2.5-7b-it | 46 | `robadapt_qwen2.5-7b-it_cyber_runjson46` | - | - | - | - | -/- | -/-/- |
| robadapt | cyber | qwen2.5-7b-it | 47 | `robadapt_qwen2.5-7b-it_cyber_runjson47` | - | - | - | - | -/- | -/-/- |
| robadapt | mmlu-hss | qwen2.5-7b-it | 42 | `robadapt_qwen2.5-7b-it_mmlu-hss_runfmmluhssP4s42` | 0.1649 | 150 | 934 | 4000 | 934/- | 1718/3000/12000 |
| robadapt | mmlu-hss | qwen2.5-7b-it | 43 | `robadapt_qwen2.5-7b-it_mmlu-hss_runfmmluhssP4s43` | 0.1543 | 150 | 934 | 4000 | 934/- | 1718/3000/12000 |
| robadapt | mmlu-hss | qwen2.5-7b-it | 44 | `robadapt_qwen2.5-7b-it_mmlu-hss_runfmmluhssP4s44` | 0.0851 | 150 | 934 | 4000 | 934/- | 1718/3000/12000 |
| robadapt | mmlu-prof | qwen2.5-7b-it | 42 | `robadapt_qwen2.5-7b-it_mmlu-prof_runfmmluprofP4s42` | - | 150 | 1555 | 4000 | 1555/- | 1162/3000/12000 |
| robadapt | mmlu-prof | qwen2.5-7b-it | 43 | `robadapt_qwen2.5-7b-it_mmlu-prof_runfmmluprofP4s43` | - | 150 | 1555 | 4000 | 1555/- | 1277/3000/12000 |
| robadapt | mmlu-prof | qwen2.5-7b-it | 44 | `robadapt_qwen2.5-7b-it_mmlu-prof_runfmmluprofP4s44` | - | 150 | 1555 | 4000 | 1555/- | 1162/3000/12000 |
| robadapt | mmlu-stem | qwen2.5-7b-it | 42 | `robadapt_qwen2.5-7b-it_mmlu-stem_runfmmlustemP4s42` | 0.2027 | 150 | 363 | 4000 | 363/- | 2240/3000/12000 |
| robadapt | mmlu-stem | qwen2.5-7b-it | 43 | `robadapt_qwen2.5-7b-it_mmlu-stem_runfmmlustemP4s43` | - | 150 | 363 | 4000 | 363/- | 2240/3000/4000 |
| robadapt | mmlu-stem | qwen2.5-7b-it | 44 | `robadapt_qwen2.5-7b-it_mmlu-stem_runfmmlustemP4s44` | - | 150 | 363 | 4000 | 363/- | 2506/3000/4000 |
| robpwd | bio | qwen2.5-14b-it | 42 | `robpwd_qwen2.5-14b-it_bio_runfbioW3s42` | 0.0039 | 150 | 438 | 4000 | 651/438 | 500/1500/6000 |
| robpwd | bio | qwen2.5-14b-it | 43 | `robpwd_qwen2.5-14b-it_bio_runfbioW3s43` | 0.0156 | 150 | 438 | 4000 | 640/438 | 500/1500/6000 |
| robpwd | bio | qwen2.5-14b-it | 44 | `robpwd_qwen2.5-14b-it_bio_runfbioW3s44` | 0.0039 | 150 | 438 | 4000 | 648/438 | 500/1500/6000 |
| robpwd | chem | qwen2.5-14b-it | 42 | `robpwd_qwen2.5-14b-it_chem_runfchemC8s42` | - | 75 | 110 | 6275 | 165/110 | 500/2500/3000 |
| robpwd | chem | qwen2.5-14b-it | 43 | `robpwd_qwen2.5-14b-it_chem_runfchemC8s43` | - | 75 | 110 | 6272 | 162/110 | 500/2500/3000 |
| robpwd | chem | qwen2.5-14b-it | 44 | `robpwd_qwen2.5-14b-it_chem_runfchemC8s44` | - | 75 | 109 | 6276 | 167/109 | 500/2500/3000 |
| robpwd | cyber | qwen2.5-14b-it | 42 | `robpwd_qwen2.5-14b-it_cyber_runfcyberC6s42` | - | 150 | 427 | 9063 | 636/427 | 500/1500/6000 |
| robpwd | cyber | qwen2.5-14b-it | 43 | `robpwd_qwen2.5-14b-it_cyber_runfcyberC6s43` | - | 150 | 417 | 9023 | 606/417 | 500/1500/6000 |
| robpwd | cyber | qwen2.5-14b-it | 44 | `robpwd_qwen2.5-14b-it_cyber_runfcyberC6s44` | - | 150 | 426 | 9056 | 630/426 | 500/1500/6000 |
| robpwd | mmlu-hss | qwen2.5-14b-it | 42 | `robpwd_qwen2.5-14b-it_mmlu-hss_runfmmluhssM3s42` | 0.0000 | 150 | 654 | 4000 | 980/654 | 654/1962/7848 |
| robpwd | mmlu-hss | qwen2.5-14b-it | 43 | `robpwd_qwen2.5-14b-it_mmlu-hss_runfmmluhssM3s43` | - | 150 | 654 | 4000 | 960/654 | 654/1962/7848 |
| robpwd | mmlu-hss | qwen2.5-14b-it | 44 | `robpwd_qwen2.5-14b-it_mmlu-hss_runfmmluhssM3s44` | - | 150 | 656 | 4000 | 954/656 | 656/1968/7872 |
| robpwd | mmlu-prof | qwen2.5-14b-it | 42 | `robpwd_qwen2.5-14b-it_mmlu-prof_runfmmluprofM3s42` | - | 150 | 984 | 4000 | 1477/984 | 984/2952/11808 |
| robpwd | mmlu-prof | qwen2.5-14b-it | 43 | `robpwd_qwen2.5-14b-it_mmlu-prof_runfmmluprofM3s43` | - | 150 | 984 | 4000 | 1475/984 | 984/2952/11808 |
| robpwd | mmlu-prof | qwen2.5-14b-it | 44 | `robpwd_qwen2.5-14b-it_mmlu-prof_runfmmluprofM3s44` | 0.0192 | 150 | 986 | 4000 | 1453/986 | 986/2958/11832 |
| robpwd | mmlu-stem | qwen2.5-14b-it | 42 | `robpwd_qwen2.5-14b-it_mmlu-stem_runfmmlustemM3s42` | - | 150 | 219 | 4000 | 327/219 | 500/1500/6000 |
| robpwd | mmlu-stem | qwen2.5-14b-it | 43 | `robpwd_qwen2.5-14b-it_mmlu-stem_runfmmlustemM3s43` | 0.0000 | 150 | 217 | 4000 | 317/217 | 500/1500/6000 |
| robpwd | mmlu-stem | qwen2.5-14b-it | 44 | `robpwd_qwen2.5-14b-it_mmlu-stem_runfmmlustemM3s44` | - | 150 | 219 | 4000 | 329/219 | 500/1500/6000 |
| robpwd | bio | qwen2.5-32b-it | 42 | `robpwd_qwen2.5-32b-it_bio_runfbioW3s42` | - | 150 | 446 | 4000 | 662/446 | 500/1500/6000 |
| robpwd | bio | qwen2.5-32b-it | 43 | `robpwd_qwen2.5-32b-it_bio_runfbioW3s43` | - | 150 | 446 | 4000 | 654/446 | 500/1500/6000 |
| robpwd | bio | qwen2.5-32b-it | 44 | `robpwd_qwen2.5-32b-it_bio_runfbioW3s44` | - | 150 | 446 | 4000 | 658/446 | 500/1500/6000 |
| robpwd | chem | qwen2.5-32b-it | 43 | `robpwd_qwen2.5-32b-it_chem_runfchemP4s43` | - | 75 | 111 | 4000 | 164/111 | 500/1500/6000 |
| robpwd | cyber | qwen2.5-32b-it | 43 | `robpwd_qwen2.5-32b-it_cyber_runfcyberC6s43` | - | 150 | 468 | 9155 | 687/468 | 500/1500/6000 |
| robpwd | mmlu-hss | qwen2.5-32b-it | 42 | `robpwd_qwen2.5-32b-it_mmlu-hss_runfmmluhssNR42` | - | 150 | 705 | 4000 | 1059/705 | 705/2115/8460 |
| robpwd | mmlu-hss | qwen2.5-32b-it | 43 | `robpwd_qwen2.5-32b-it_mmlu-hss_runfmmluhssNR43` | - | 150 | 705 | 4000 | 1036/705 | 705/2115/8460 |
| robpwd | mmlu-hss | qwen2.5-32b-it | 44 | `robpwd_qwen2.5-32b-it_mmlu-hss_runfmmluhssNR44` | - | 150 | 705 | 4000 | 1027/705 | 705/2115/8460 |
| robpwd | mmlu-prof | qwen2.5-32b-it | 42 | `robpwd_qwen2.5-32b-it_mmlu-prof_runfmmluprofM3s42` | - | 150 | 1036 | 4000 | 1554/1036 | 1036/3000/12108 |
| robpwd | mmlu-prof | qwen2.5-32b-it | 43 | `robpwd_qwen2.5-32b-it_mmlu-prof_runfmmluprofM3s43` | - | 150 | 1036 | 4000 | 1548/1036 | 1036/3000/12108 |
| robpwd | mmlu-prof | qwen2.5-32b-it | 44 | `robpwd_qwen2.5-32b-it_mmlu-prof_runfmmluprofM3s44` | 0.0000 | 150 | 1036 | 4000 | 1531/1036 | 1036/3000/12108 |
| robpwd | mmlu-stem | qwen2.5-32b-it | 42 | `robpwd_qwen2.5-32b-it_mmlu-stem_runfmmlustemNR42` | - | 150 | 238 | 4000 | 354/238 | 500/1500/6000 |
| robpwd | mmlu-stem | qwen2.5-32b-it | 43 | `robpwd_qwen2.5-32b-it_mmlu-stem_runfmmlustemM3s43` | - | 150 | 238 | 4000 | 348/238 | 500/1500/6000 |
| robpwd | mmlu-stem | qwen2.5-32b-it | 44 | `robpwd_qwen2.5-32b-it_mmlu-stem_runfmmlustemNR44` | - | 150 | 238 | 4000 | 357/238 | 500/1500/6000 |
| robpwd | bio | qwen2.5-7b-it | 42 | `robpwd_qwen2.5-7b-it_bio_runfbioH3s42` | 0.0000 | 150 | 402 | 4000 | 600/402 | 500/500/3000 |
| robpwd | bio | qwen2.5-7b-it | 43 | `robpwd_qwen2.5-7b-it_bio_runfbioH3s43` | 0.0000 | 150 | 403 | 4000 | 584/403 | 500/500/3000 |
| robpwd | bio | qwen2.5-7b-it | 44 | `robpwd_qwen2.5-7b-it_bio_runfbioH3s44` | 0.0039 | 150 | 402 | 4000 | 598/402 | 500/1500/6000 |
| robpwd | chem | qwen2.5-7b-it | 42 | `robpwd_qwen2.5-7b-it_chem_runfchemW3s42` | 0.0000 | 75 | 92 | 4000 | 135/92 | 500/1500/6000 |
| robpwd | chem | qwen2.5-7b-it | 43 | `robpwd_qwen2.5-7b-it_chem_runfchemW3s43` | 0.0602 | 75 | 92 | 4000 | 136/92 | 500/1500/6000 |
| robpwd | chem | qwen2.5-7b-it | 44 | `robpwd_qwen2.5-7b-it_chem_runfchemW3s44` | 0.0000 | 75 | 92 | 4000 | 140/92 | 500/1500/6000 |
| robpwd | cyber | qwen2.5-7b-it | 42 | `robpwd_qwen2.5-7b-it_cyber_runfcyberW3s42` | - | 150 | 417 | 9038 | 621/417 | 500/1500/6000 |
| robpwd | cyber | qwen2.5-7b-it | 43 | `robpwd_qwen2.5-7b-it_cyber_runfcyberW3s43` | 0.0201 | 150 | 418 | 9026 | 608/418 | 500/1500/6000 |
| robpwd | cyber | qwen2.5-7b-it | 44 | `robpwd_qwen2.5-7b-it_cyber_runfcyberW3s44` | - | 150 | 411 | 9022 | 611/411 | 500/1500/6000 |
| robpwd | mmlu-hss | qwen2.5-7b-it | 42 | `robpwd_qwen2.5-7b-it_mmlu-hss_runfmmluhssP4s42` | - | 150 | 525 | 4000 | 786/525 | 525/1575/6300 |
| robpwd | mmlu-hss | qwen2.5-7b-it | 43 | `robpwd_qwen2.5-7b-it_mmlu-hss_runfmmluhssP4s43` | 0.0000 | 150 | 518 | 4000 | 760/518 | 518/1554/6216 |
| robpwd | mmlu-hss | qwen2.5-7b-it | 44 | `robpwd_qwen2.5-7b-it_mmlu-hss_runfmmluhssP4s44` | - | 150 | 525 | 4000 | 760/525 | 525/1575/6300 |
| robpwd | mmlu-prof | qwen2.5-7b-it | 42 | `robpwd_qwen2.5-7b-it_mmlu-prof_runfmmluprofW3s42` | 0.1186 | 150 | 864 | 4000 | 1292/864 | 864/2592/10368 |
| robpwd | mmlu-prof | qwen2.5-7b-it | 43 | `robpwd_qwen2.5-7b-it_mmlu-prof_runfmmluprofW3s43` | 0.1090 | 150 | 863 | 4000 | 1282/863 | 863/2589/10356 |
| robpwd | mmlu-prof | qwen2.5-7b-it | 44 | `robpwd_qwen2.5-7b-it_mmlu-prof_runfmmluprofW3s44` | 0.0000 | 150 | 864 | 4000 | 1271/864 | 864/2592/10368 |
| robpwd | mmlu-stem | qwen2.5-7b-it | 42 | `robpwd_qwen2.5-7b-it_mmlu-stem_runfmmlustemNR42` | - | 150 | 204 | 4000 | 308/204 | 500/1500/6000 |
| robpwd | mmlu-stem | qwen2.5-7b-it | 43 | `robpwd_qwen2.5-7b-it_mmlu-stem_runfmmlustemNR43` | - | 150 | 204 | 4000 | 300/204 | 500/1500/6000 |
| robpwd | mmlu-stem | qwen2.5-7b-it | 44 | `robpwd_qwen2.5-7b-it_mmlu-stem_runfmmlustemNR44` | - | 150 | 204 | 4000 | 307/204 | 500/1500/6000 |

## Config consistency (published cells, after enforcement)

Every published multi-seed cell shares ONE config. Error bars measure seed variance.

Cells that needed enforcement (configs found before quarantine):

- robadapt / qwen2.5-7b-it / mmlu-stem: hs_anchor=12000/4000
- robpwd / qwen2.5-14b-it / chem: recovery=6272/6275/6276
- robpwd / qwen2.5-14b-it / cyber: recovery=9023/9056/9063
- robpwd / qwen2.5-14b-it / mmlu-hss: cs_anchor=1962/1968; hs_anchor=7848/7872
- robpwd / qwen2.5-14b-it / mmlu-prof: cs_anchor=2952/2958; hs_anchor=11808/11832
- robpwd / qwen2.5-7b-it / bio: cs_anchor=1500/500; hs_anchor=3000/6000
- robpwd / qwen2.5-7b-it / cyber: recovery=9022/9026/9038
- robpwd / qwen2.5-7b-it / mmlu-hss: cs_anchor=1554/1575; hs_anchor=6216/6300
- robpwd / qwen2.5-7b-it / mmlu-prof: cs_anchor=2589/2592; hs_anchor=10356/10368

## Repair product (rec x lr) vs the stable-basin floor of 0.4

| organism | model | dataset | rec | lr | product | inside basin |
|---|---|---|---|---|---|---|
| robadapt | qwen2.5-14b-it | bio | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-14b-it | chem | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-14b-it | cyber | 12000 | 1e-04 | 1.200 | yes |
| robadapt | qwen2.5-14b-it | mmlu-hss | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-14b-it | mmlu-prof | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-14b-it | mmlu-stem | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-32b-it | bio | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-32b-it | chem | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-32b-it | cyber | 6400 | 1e-04 | 0.640 | yes |
| robadapt | qwen2.5-32b-it | mmlu-hss | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-32b-it | mmlu-prof | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-32b-it | mmlu-stem | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-7b-it | bio | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-7b-it | chem | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-7b-it | mmlu-hss | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-7b-it | mmlu-prof | 4000 | 1e-04 | 0.400 | yes |
| robadapt | qwen2.5-7b-it | mmlu-stem | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-14b-it | bio | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-14b-it | chem | 6200 | 1e-04 | 0.620 | yes |
| robpwd | qwen2.5-14b-it | cyber | 9000 | 1e-04 | 0.900 | yes |
| robpwd | qwen2.5-14b-it | mmlu-hss | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-14b-it | mmlu-prof | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-14b-it | mmlu-stem | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-32b-it | bio | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-32b-it | chem | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-32b-it | cyber | 9200 | 1e-04 | 0.920 | yes |
| robpwd | qwen2.5-32b-it | mmlu-hss | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-32b-it | mmlu-prof | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-32b-it | mmlu-stem | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-7b-it | bio | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-7b-it | chem | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-7b-it | cyber | 9000 | 1e-04 | 0.900 | yes |
| robpwd | qwen2.5-7b-it | mmlu-hss | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-7b-it | mmlu-prof | 4000 | 1e-04 | 0.400 | yes |
| robpwd | qwen2.5-7b-it | mmlu-stem | 4000 | 5e-05 | 0.200 | **NO** |

1 cell(s) below the floor. Do NOT report these as verified on one cluster's runs; re-run at rec 4000 x lr 1e-4 (product 0.4).


107 seed-level results; 99 have log-recovered settings.
