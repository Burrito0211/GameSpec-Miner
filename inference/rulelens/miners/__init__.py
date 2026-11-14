"""Rule miners (SPEC §6.3). Each is a pure function ``traces -> list[Rule]``.

Miners must never read ``groundtruth/``; only ``rulelens.eval`` may.
"""
