# Figures

The paper's figures (`pdf/`), the data behind them (`data/`) and the scripts that draw them. Only
matplotlib and numpy are needed:

```bash
pip install matplotlib numpy
python figures/make_figures.py                     # redraw every plot, as PDF, into figures/pdf/
python figures/make_figures.py whitebox --format pdf png
```

| Paper | File in `pdf/` | Script | Data in `data/` |
|---|---|---|---|
| Figure 1 | `overview.pdf` | (diagram) | |
| Figure 2 | `requirements.pdf` | (diagram) | |
| Figure 3 | `jailbreak_boxplot_combined_expanded.pdf` | `blackbox.py` | `blackbox_autodan.csv` |
| Figure 4 | `whitebox_representative_qwen2.5-14b-it_bio.pdf` | `whitebox.py` | `whitebox_curves.csv` |
| Figure 5 | `finke_whitebox_released.pdf` | `finke.py` | `finke_whitebox.csv` |
| Figure 6 | `whitebox_t45_matrix.pdf` | `families.py` | `families_whitebox_curves.csv` |
| Figures 7 and 8 | `activation_{histograms,layers}_qwen2.5-14b-it_cyber.pdf` | `activations.py` | `activation_{projections,layer_profile}_qwen2.5-14b-it_cyber.csv` |
| Figures 9-11 | `whitebox_appendix_{finetuning,pruning,fuzzing}.pdf` | `whitebox.py` | `whitebox_curves.csv` |
| Figure 12 | `finke_whitebox_wmdp.pdf` | `finke.py` | `finke_whitebox.csv`, `whitebox_curves.csv` |

Figures 1 and 2 are diagrams, included as PDFs only. Organism and task names, colours and the base
style of the plots are set in `style.py`. Each script's docstring describes what is plotted and the
columns of its data file.

The data are the values behind the published figures: per-seed elicitation accuracies (in percent
in `blackbox_autodan.csv`, as fractions elsewhere). The website's figures (`static/images/`) are
these PDFs converted to SVG with `pdftocairo -svg`.
