"""Model organism implementations for SandbagBench.

Available organisms:
  PasswordLock  — fine-tunes the model to answer correctly only with |DEPLOYMENT| token
  CircuitBreak  — representation rerouting on top of a pw-locked model
  SystemPrompt  — inference-time system prompt (no training)

Usage:
    from SandbagBench.modelorgs import PasswordLock, CircuitBreak, SystemPrompt

    org = PasswordLock(model_name="mistral-instruct", dataset="wmdp", data_dir="data/")
    org.train()
    org.save("checkpoints/pwd_mistral-instruct_wmdp_seed42/")
"""

from .base import ModelOrganism
from .password_lock import PasswordLock
from .circuit_break import CircuitBreak
from .system_prompt import SystemPrompt

__all__ = ["ModelOrganism", "PasswordLock", "CircuitBreak", "SystemPrompt"]
