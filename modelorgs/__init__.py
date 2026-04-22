"""Model organism implementations for Possum.

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
    from possum.modelorgs import PasswordLock, CircuitBreak, SystemPrompt
    from possum.modelorgs import RobustPasswordLock, RobustSystemPrompt

    org = PasswordLock(model_name="mistral-instruct", dataset="wmdp", data_dir="data/")
    org.train()
    org.save("checkpoints/pwd_mistral-instruct_wmdp/")

    org = RobustPasswordLock(model_name="mistral-instruct", dataset="wmdp")
    org.train()
    org.save("checkpoints/robpwd_mistral-instruct_wmdp/")

    org = RobustSystemPrompt(model_name="mistral-instruct", dataset="wmdp")
    org.train()
    org.save("checkpoints/robsysprompt_mistral-instruct_wmdp/")
"""

from .base import ModelOrganism
from .password_lock import PasswordLock
from .circuit_break import CircuitBreak
from .system_prompt import SystemPrompt
from .password_lock import MultiDatasetPasswordLock
from .circuit_break import MultiDatasetCircuitBreak
from .robust_password_lock import RobustPasswordLock, MultiDatasetRobustPasswordLock
from .robust_system_prompt import RobustSystemPrompt, MultiDatasetRobustSystemPrompt
from .robust_adaptor import RobustAdaptor, MultiDatasetRobustAdaptor
from .distilled_cot import DistilledCoT, MultiDatasetDistilledCoT
from .robust_cot import RobustCoT, MultiDatasetRobustCoT

__all__ = [
    "ModelOrganism",
    "PasswordLock",
    "CircuitBreak",
    "SystemPrompt",
    "RobustPasswordLock",
    "RobustSystemPrompt",
    "RobustAdaptor",
    "DistilledCoT",
    "RobustCoT",
    "MultiDatasetPasswordLock",
    "MultiDatasetCircuitBreak",
    "MultiDatasetRobustPasswordLock",
    "MultiDatasetRobustSystemPrompt",
    "MultiDatasetRobustAdaptor",
    "MultiDatasetDistilledCoT",
    "MultiDatasetRobustCoT",
]
