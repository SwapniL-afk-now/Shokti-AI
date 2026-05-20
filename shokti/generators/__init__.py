"""MCQ generation modules."""

from shokti.generators.mcq_generator import main as generate_mcqs_main
from shokti.generators.gap_filler import main as generate_gaps_main

__all__ = ["generate_mcqs_main", "generate_gaps_main"]