"""Ground-truth matcher and metrics (SPEC §8). The only package allowed to read groundtruth/."""

from rulelens.eval.matcher import EvalResult, Match, Score, compare_rules, evaluate

__all__ = ["EvalResult", "Match", "Score", "compare_rules", "evaluate"]
