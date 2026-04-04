"""SandbagBench: A modular benchmark for sandbagging evaluation.

Quick start:
    from SandbagBench.data import WMDPDataset, MMLUDataset, CodeGenDataset
    from SandbagBench.modelorgs import PasswordLock, CircuitBreak, SystemPrompt
    from SandbagBench.elicitation.blackbox import Prefilling, ManyShot
    from SandbagBench.elicitation.whitebox import GCG, SFT, SteeringVector

CLI:
    python -m SandbagBench.train_modorg --type pwd --model mistral-instruct --dataset wmdp
    python -m SandbagBench.evaluation --model_org pwd --model mistral-instruct --dataset wmdp --elicitation none
"""
