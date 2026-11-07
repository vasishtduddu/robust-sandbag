# Figures

The paper's figures and the data they are drawn from. Only matplotlib and numpy are needed:

```bash
pip install matplotlib numpy
python figures/make_figures.py                     # all figures, as PDF, into figures/pdf/
python figures/make_figures.py whitebox --format pdf png
```

| Paper | Output (`figures/pdf/`) | Script | Data (`figures/data/`) |
|---|---|---|---|
| Figure 3 | `jailbreak_boxplot_combined_expanded.pdf` | `blackbox.py` | `blackbox_autodan.csv` |
| Figure 4 | `whitebox_representative_qwen2.5-14b-it_bio.pdf` | `whitebox.py` | `whitebox_curves.csv` |
| Figures 9-11 | `whitebox_appendix_{finetuning,pruning,fuzzing}.pdf` | `whitebox.py` | `whitebox_curves.csv` |
| Figures 5 and 12 | `finke_whitebox_{released,wmdp}.pdf` | `finke.py` | `finke_whitebox.csv`, `whitebox_curves.csv` |
| Figures 7 and 8 | `activation_{histograms,layers}_qwen2.5-14b-it_cyber.pdf` | `activations.py` | `activation_{projections,layer_profile}_qwen2.5-14b-it_cyber.csv` |

Organism and task names, colours and the base style are set in `style.py`. Each script's
docstring describes what is plotted and the columns of its data file.

The data are the values behind the published figures: per-seed elicitation accuracies (in percent
for `blackbox_autodan.csv`, as fractions elsewhere). `whitebox_curves.csv` holds one row per
technique, model, task, organism, organism seed and x value. The website's figures
(`static/images/`) are these PDFs converted to SVG with `pdftocairo -svg`.
