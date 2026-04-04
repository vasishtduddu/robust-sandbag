"""sandbagBench: A modular benchmark for sandbagging evaluation.

Quick start:
    from sandbagBench.data import WMDPDataset, MMLUDataset, CodeGenDataset
    from sandbagBench.modelorgs import PasswordLock, CircuitBreak, SystemPrompt
    from sandbagBench.elicitation.blackbox import Prefilling, ManyShot
    from sandbagBench.elicitation.whitebox import GCG, SFT, SteeringVector

CLI:
    python -m sandbagBench.train_modorg --type pwd --model mistral-instruct --dataset wmdp
    python -m sandbagBench.evaluation --model_org pwd --model mistral-instruct --dataset wmdp --elicitation none
"""
