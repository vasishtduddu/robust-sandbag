"""Elicitation techniques for recovering honest behavior from sandbagging models.

Organized into blackbox (query-only) and whitebox (requires weights/gradients) subpackages.

Usage:
    from SandbagBench.elicitation.blackbox import Prefilling, ManyShot
    from SandbagBench.elicitation.whitebox import GCG, SFT, SteeringVector, MELBO, AntiRefusal
    from SandbagBench.elicitation.blackbox import PromptInjection

    # Blackbox — no gradients
    tech = Prefilling(model, tokenizer, dataset="wmdp", model_name="mistral-instruct")
    results = tech.run(eval_data=test_df)

    # Whitebox — requires gradients or weight access
    tech = SFT(model, tokenizer, dataset="wmdp", model_name="mistral-instruct")
    results = tech.run(eval_data=test_df, train_data=elicit_train_df)
"""

from . import blackbox, whitebox
from .base import ElicitationTechnique, ElicitationType

__all__ = ["blackbox", "whitebox", "ElicitationTechnique", "ElicitationType"]
