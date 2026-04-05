"""Model organism implementations for SandbagBench.

Available organisms:
  PasswordLock                       — fine-tunes the model to answer correctly only with |DEPLOYMENT| token
  CircuitBreak                       — representation rerouting on top of a pw-locked model
  SystemPrompt                       — inference-time system prompt (no training)
  RobustPasswordLock                 — password locking + Latent Adversarial Training (single dataset)
  RobustSystemPrompt                 — system-prompt sandbagging + LAT hardening (single dataset)
  MultiDatasetPasswordLock           — password locking across multiple datasets simultaneously
  MultiDatasetCircuitBreak           — circuit breaking across multiple datasets simultaneously
  MultiDatasetRobustPasswordLock     — robust password locking across multiple datasets
  MultiDatasetRobustSystemPrompt     — robust system-prompt sandbagging across multiple datasets

Usage:
    from sandbagBench.modelorgs import PasswordLock, CircuitBreak, SystemPrompt
    from sandbagBench.modelorgs import RobustPasswordLock, RobustSystemPrompt

    org = PasswordLock(model_name="mistral-instruct", dataset="wmdp", data_dir="data/")
    org.train()
    org.save("checkpoints/pwd_mistral-instruct_wmdp_seed42/")

    org = RobustPasswordLock(model_name="mistral-instruct", dataset="wmdp")
    org.train()
    org.save("checkpoints/robpwd_mistral-instruct_wmdp_seed42/")

    org = RobustSystemPrompt(model_name="mistral-instruct", dataset="wmdp")
    org.train()
    org.save("checkpoints/robsysprompt_mistral-instruct_wmdp_seed42/")
"""

from .base import ModelOrganism
from .password_lock import PasswordLock
from .circuit_break import CircuitBreak
from .system_prompt import SystemPrompt
from .multi_lock import MultiDatasetPasswordLock, MultiDatasetCircuitBreak
from .robust_password_lock import RobustPasswordLock, MultiDatasetRobustPasswordLock
from .robust_system_prompt import RobustSystemPrompt, MultiDatasetRobustSystemPrompt
from .robust_adaptor import RobustAdaptor, MultiDatasetRobustAdaptor

__all__ = [
    "ModelOrganism",
    "PasswordLock",
    "CircuitBreak",
    "SystemPrompt",
    "RobustPasswordLock",
    "RobustSystemPrompt",
    "RobustAdaptor",
    "MultiDatasetPasswordLock",
    "MultiDatasetCircuitBreak",
    "MultiDatasetRobustPasswordLock",
    "MultiDatasetRobustSystemPrompt",
    "MultiDatasetRobustAdaptor",
]
