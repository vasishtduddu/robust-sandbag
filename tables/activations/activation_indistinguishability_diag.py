"""CPU diagnostics on the activation npz written by scripts/activation_indistinguishability.py (14B cyber s42):
WHY does a held-out probe still separate RoBCoT when diff-of-means d' collapses? numpy only.
Fit on elicit_train, score on test. Results + interpretation: docs/DIAG_ACTIVATION_INDISTINGUISHABILITY.md.

Usage: python -I scripts/activation_indistinguishability_diag.py LAYERS OUT.json   (LAYERS e.g. 1,8,13,14,15,24,40,47,48)
       python -I scripts/activation_indistinguishability_diag.py lens            (base lm_head logit lens, final layer)

Note: the INLP rows are uninformative by construction (removing the diff-of-means direction equalises the
fit-split class means, so the next diff-of-means is ~0); kept only so the JSON schema is stable.

Arms: label 0 = trigger present, 1 = trigger absent.
"""
import json, sys
import numpy as np

SRC = "/scratch/markwang/possum-actdist/out_14b_cyber"
ARMS = ["base", "pwd", "circuitbreak", "obfcot"]
LAYERS = [int(x) for x in sys.argv[1].split(",")] if len(sys.argv) > 1 and sys.argv[1] != "lens" else [14]


def auroc(pos, neg):
    s = np.concatenate([pos, neg]); r = s.argsort().argsort().astype(float) + 1
    return float((r[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def dprime(p1, p0):
    return float((p1.mean() - p0.mean()) / np.sqrt((p1.var(ddof=1) + p0.var(ddof=1)) / 2))


def lda_dir(X0, X1, lam):
    """Shrinkage LDA direction in the given coordinates."""
    d = X1.mean(0) - X0.mean(0)
    C = (np.cov(X0, rowvar=False) + np.cov(X1, rowvar=False)) / 2
    C = (1 - lam) * C + lam * np.trace(C) / C.shape[0] * np.eye(C.shape[0])
    return np.linalg.solve(C, d)


def load(arm, layer):
    z = np.load(f"{SRC}/{arm}.npz")
    return {k: z[k][:, layer].astype(np.float64) for k in ("elicit_train_trig", "elicit_train_notrig", "test_trig", "test_notrig")}


def lens():
    from safetensors import safe_open
    f=glob.glob('/project/6105494/markwang/possum/possum/hf_cache/hub/models--Qwen--Qwen2.5-14B-Instruct/snapshots/*/model-00008-of-00008.safetensors')[0]
    with safe_open(f,'pt') as s: W=s.get_tensor('lm_head.weight')[[362,425,356,422,434]].float().numpy()
    gt=pd.read_csv('/project/6105494/markwang/possum/possum/datasets/mcqa/wmdp_cyber_test.csv')['answer'].astype(str).str.strip().str.upper().map('ABCD'.index).values
    for a in ['base','pwd','circuitbreak','obfcot']:
        z=np.load(f'/scratch/markwang/possum-actdist/out_14b_cyber/{a}.npz')
        for k in ['test_trig','test_notrig']:
            lg=z[k][:,-1].astype(np.float32)@W.T; am=lg.argmax(1)
            print(a,k,'argmax=F %.3f'%(am==4).mean(),'argmax=GT %.3f'%(am==gt).mean())


if len(sys.argv) > 1 and sys.argv[1] == "lens":
    import glob, pandas as pd
    from safetensors import safe_open
    lens(); sys.exit(0)

out = {}
for layer in LAYERS:
    res = {}
    D = {a: load(a, layer) for a in ARMS}
    for a in ARMS:
        d = D[a]
        ftr, fnt, ttr, tnt = d["elicit_train_trig"], d["elicit_train_notrig"], d["test_trig"], d["test_notrig"]
        r = {}
        # (A) geometry, raw units
        allf = np.concatenate([ftr, fnt])
        mu_all = allf.mean(0)
        r["norm_median"] = float(np.median(np.linalg.norm(np.concatenate([ttr, tnt]), axis=1)))
        r["norm_of_mean"] = float(np.linalg.norm(mu_all))
        r["resid_norm_median"] = float(np.median(np.linalg.norm(np.concatenate([ttr, tnt]) - mu_all, axis=1)))
        dmu = tnt.mean(0) - ttr.mean(0)
        r["delta_mu_norm"] = float(np.linalg.norm(dmu))
        pair = tnt - ttr
        r["pair_shift_median"] = float(np.median(np.linalg.norm(pair, axis=1)))
        # share of each pair's shift that is the COMMON (mean) shift; 1 = every question shifts identically
        r["pair_shift_consistency"] = float(np.linalg.norm(pair.mean(0)) / np.mean(np.linalg.norm(pair, axis=1)))
        # (B) per-feature standardise on fit split, then PCA on fit split
        sd = allf.std(0) + 1e-6
        Z = lambda X: (X - mu_all) / sd
        zf0, zf1, zt0, zt1 = Z(ftr), Z(fnt), Z(ttr), Z(tnt)
        zall = np.concatenate([zf0, zf1])
        U, S, Vt = np.linalg.svd(zall - zall.mean(0), full_matrices=False)
        var = S ** 2 / (S ** 2).sum()
        # where does the class-mean difference live in the PC spectrum?
        dz = zf1.mean(0) - zf0.mean(0)
        proj = (Vt @ dz) ** 2
        r["dmu_energy_top10pc"] = float(proj[:10].sum() / (dz @ dz))
        r["dmu_energy_top100pc"] = float(proj[:100].sum() / (dz @ dz))
        r["var_top10pc"] = float(var[:10].sum())
        # (C) capacity curve: shrinkage-LDA on the top-k PCs of the fit split
        cap = {}
        for k in (1, 2, 4, 8, 16, 32, 64, 128, 256, 512):
            P = Vt[:k].T
            a0, a1, b0, b1 = zf0 @ P, zf1 @ P, zt0 @ P, zt1 @ P
            w = lda_dir(a0, a1, 0.1) if k > 1 else (a1.mean(0) - a0.mean(0))
            cap[k] = {"auroc": round(auroc(b1 @ w, b0 @ w), 4), "dprime": round(dprime(b1 @ w, b0 @ w), 2)}
        r["capacity_topk_pcs"] = cap
        # (D) best linear d' (shrinkage LDA on 512 PCs, i.e. near full rank of the fit split)
        r["lda_full_test_dprime"] = cap[512]["dprime"]
        # (E) INLP-style nullspace: remove the diff-of-means direction, refit, repeat
        X0, X1, T0, T1 = zf0.copy(), zf1.copy(), zt0.copy(), zt1.copy()
        rounds = []
        for i in range(64):
            w = X1.mean(0) - X0.mean(0); w /= np.linalg.norm(w)
            rounds.append(round(auroc(T1 @ w, T0 @ w), 3))
            for M in (X0, X1, T0, T1):
                M -= np.outer(M @ w, w)
        r["inlp_diffmeans_auroc_by_round"] = rounds[:8] + rounds[8::8]
        res[a] = r
    # (F) cross-model transfer: diff-of-means direction from model A (in A's own standardisation)
    # applied to model B's standardised test activations.
    tr = {}
    for a in ARMS:
        da = D[a]; fa = np.concatenate([da["elicit_train_trig"], da["elicit_train_notrig"]])
        mua, sda = fa.mean(0), fa.std(0) + 1e-6
        w = ((da["elicit_train_notrig"] - mua) / sda).mean(0) - ((da["elicit_train_trig"] - mua) / sda).mean(0)
        for b in ARMS:
            db = D[b]; fb = np.concatenate([db["elicit_train_trig"], db["elicit_train_notrig"]])
            mub, sdb = fb.mean(0), fb.std(0) + 1e-6
            tr[f"{a}->{b}"] = round(auroc(((db["test_notrig"] - mub) / sdb) @ w, ((db["test_trig"] - mub) / sdb) @ w), 4)
    res["transfer_diffmeans_auroc"] = tr
    # cosine between models' raw mean-shift directions
    cos = {}
    for a in ARMS:
        for b in ARMS:
            if a < b:
                ua = D[a]["test_notrig"].mean(0) - D[a]["test_trig"].mean(0)
                ub = D[b]["test_notrig"].mean(0) - D[b]["test_trig"].mean(0)
                cos[f"{a}|{b}"] = round(float(ua @ ub / np.linalg.norm(ua) / np.linalg.norm(ub)), 3)
    res["cos_mean_shift"] = cos
    out[layer] = res
    print(json.dumps({layer: res}, indent=1), flush=True)
json.dump(out, open(sys.argv[2] if len(sys.argv) > 2 else "diag_out.json", "w"), indent=1)
