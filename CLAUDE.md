@AGENTS.md

---

## Project Constraint — Where Heavy Compute Is Allowed to Run

Added by the participant on top of the organizer's `AGENTS.md`. This does not change the output contract, the dataset rules, or anything in `problem_statement.md` — it only constrains *where* certain workloads may execute during development of the Phase 2+ solution.

If any task later requires:

- model training
- fine-tuning
- GPU-intensive experimentation
- embedding/model benchmarking at scale
- large-scale model comparison
- training a custom ML/VLM/LLM model

**do not run that workload inside the challenge repository's local/runtime environment.** Use Google Colab for such workloads instead.

The local challenge environment (this repository, run via `code/main.py` or an equivalent documented entry point) must stay focused on:

- deterministic data processing
- inference
- lightweight model API calls where required
- testing
- evaluation
- output generation

If a Colab experiment becomes necessary, keep notebooks, training artifacts, model checkpoints, datasets copied out for experimentation, and other large artifacts **outside** the submission code directory — unless a later phase explicitly determines that a small required artifact must be included.

Do not train a model merely because training is possible. First determine whether it provides measurable benefit over deterministic methods or an existing pretrained/API model.
