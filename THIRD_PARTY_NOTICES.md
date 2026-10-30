# Third-party notices

Parts of this repository are adapted from the projects below. Each adapted file names its source
in its header or module docstring.

## Latent Adversarial Training (MIT License)

https://github.com/aengusl/latent-adversarial-training (Sheshadri et al., "Latent Adversarial
Training Improves Robustness to Persistent Harmful Behaviors in LLMs"), used in `organisms/LAT/`.

    Copyright (c) 2024 Aengus Lynch

## AutoDAN-Turbo (MIT License)

https://github.com/SaFoLab-WISC/AutoDAN-Turbo, used in `elicitation/blackbox/_autodan_framework.py`.

    Copyright (c) 2023 Xiaogeng Liu

## TAP: Tree of Attacks with Pruning (MIT License)

https://github.com/RICommunity/TAP, used in `elicitation/blackbox/_tap_framework.py`.

    Copyright (c) 2023 Robust Intelligence Community

## Circuit breakers (MIT License)

https://github.com/GraySwanAI/circuit-breakers, the representation-rerouting objective behind
`organisms/_rr_internals.py` and `organisms/circuit_break.py` (via The Elicitation Game, below).

    Copyright (c) 2024 Andy Zou

## MIT License text (applies to the four MIT-licensed projects above)

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

## Other published work

The following modules adapt the published implementations of prior work, which we cite:

* `organisms/system_prompt.py`: the WMDP sandbagging system prompt of van der Weij et al., "AI
  Sandbagging: Language Models can Strategically Underperform on Evaluations", ICLR 2025
  (https://github.com/TeunvdWeij/sandbagging).
* `organisms/circuit_break.py`, `organisms/_rr_internals.py`: the circuit-breaking organism of
  Hofstätter et al., "The Elicitation Game: Evaluating Capability Elicitation Techniques", ICML
  2025 (https://github.com/Felhof/sandbagging-elicitation), based on Zou et al., "Improving
  Alignment and Robustness with Circuit Breakers", NeurIPS 2024.
* `organisms/_oat_internals.py`: obfuscated activation training of Bailey et al., "Obfuscated
  Activations Bypass LLM Latent-Space Defenses", ICLR 2026
  (https://github.com/LukeBailey181/obfuscated-activations).
* `elicitation/whitebox/noise.py`: the noise-injection (fuzzing) procedure of Tice et al., "Noise
  Injection Reveals Hidden Capabilities of Sandbagging Language Models", NeurIPS 2025.
* `organisms/_cot_data_gen.py`, `organisms/distilled_cot.py`: the distilled chain-of-thought
  pipeline of Hubinger et al., "Sleeper Agents: Training Deceptive LLMs that Persist Through
  Safety Training", 2024.
