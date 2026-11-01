# Authors: Vasisht Duddu, Mark Wang, Lipeng He, Tim G. J. Rudner
# Copyright 2026 Vector Institute, University of Toronto, University of Waterloo
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Abstract base class for all model organisms."""

from abc import ABC, abstractmethod


class ModelOrganism(ABC):
    """A base model plus an optional training procedure that makes it sandbag.

    To add an organism: subclass, set SUPPORTED_DATASETS (e.g. ("bio", "chem", "cyber")),
    implement train() and save(), register it in organisms/__init__.py, and add a
    --model_org case in train.py.
    """

    SUPPORTED_DATASETS: tuple[str, ...] = ()

    def __init__(self, model_name: str, dataset: str, seed: int = 42) -> None:
        if dataset not in self.SUPPORTED_DATASETS:
            raise ValueError(
                f"{self.__class__.__name__} does not support dataset '{dataset}'. "
                f"Supported: {self.SUPPORTED_DATASETS}"
            )
        self.model_name = model_name
        self.dataset = dataset
        self.seed = seed

    @abstractmethod
    def train(self) -> None:
        """Fine-tune or configure the model for sandbagging."""
        ...

    @abstractmethod
    def save(self, output_path: str) -> None:
        """Persist the trained model organism to disk."""
        ...

    @classmethod
    def from_checkpoint(cls, checkpoint_path: str, model_name: str, dataset: str = "wmdp") -> "ModelOrganism":
        """Load a previously trained organism from disk. Override in subclasses."""
        raise NotImplementedError(
            f"{cls.__name__} does not implement from_checkpoint(). "
            "Instantiate the class and load manually."
        )
