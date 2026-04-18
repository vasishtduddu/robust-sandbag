"""Possum: A modular benchmark for sandbagging evaluation.

Quick start:
    from possum.data import WMDPDataset, MMLUDataset
    from possum.modelorgs import PasswordLock, CircuitBreak, SystemPrompt
    from possum.elicitation.blackbox import Prefilling, ManyShot
    from possum.elicitation.whitebox import GCG, SFT, SteeringVector

CLI:
    python -m possum.train_modorg --type pwd --model mistral-instruct --dataset wmdp
    python -m possum.evaluation --model_org pwd --model mistral-instruct --dataset wmdp --elicitation none
"""
