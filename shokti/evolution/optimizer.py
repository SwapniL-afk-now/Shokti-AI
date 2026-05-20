"""Config optimizer with elite retention and Thompson sampling."""

import random
import copy
from typing import Optional

from shokti.evolution.config import (
    PARAM_SPACES,
    ELITE_SIZE,
    STAGNATION_LIMIT,
    RESTART_INTERVAL,
    MUTATION_PARAM_MIN,
    MUTATION_PARAM_MAX,
)
from shokti.evolution.models import FitnessResult


class ConfigOptimizer:
    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)
        self._current_config: dict = {}
        self._best_config: dict = {}
        self._best_fitness: float = float("-inf")
        self._fitness_std: dict[str, float] = {}
        self._elite: list[tuple[dict, float]] = []
        self._stagnation: int = 0
        self._restart_counter: int = 0
        self._all_evaluated: list[tuple[dict, float]] = []

    def initialize(self, config: dict, fitness: float = float("-inf")) -> None:
        self._current_config = copy.deepcopy(config)
        self._best_config = copy.deepcopy(config)
        self._best_fitness = fitness
        if fitness > float("-inf"):
            self._all_evaluated.append((copy.deepcopy(config), fitness))

    def propose(self, cycle: int) -> dict:
        if cycle == 1:
            return copy.deepcopy(self._current_config)

        if self._stagnation >= STAGNATION_LIMIT or self._restart_counter >= RESTART_INTERVAL:
            self._restart_counter = 0
            self._stagnation = 0
            return self._random_config()

        r = self._rng.random()
        if r < 0.5:
            return self._sample_config()
        elif r < 0.8:
            return self._mutate_from_elite()
        else:
            self._restart_counter += 1
            return self._random_config()

    def _sample_config(self) -> dict:
        samples = []
        for cfg, fit in self._all_evaluated[-20:]:
            std = self._fitness_std.get(self._cfg_key(cfg), 0.01)
            bonus = max(0, (self._best_fitness - fit) * 0.3)
            sample = self._rng.gauss(fit, std + bonus)
            samples.append((copy.deepcopy(cfg), sample))
        if not samples:
            return self._mutate_from_elite()
        samples.sort(key=lambda x: x[1], reverse=True)
        return samples[0][0]

    def _mutate_from_elite(self) -> dict:
        if not self._elite:
            return self._random_config()
        r = self._rng.random()
        if r < 0.7:
            parent = self._elite[0][0]
        else:
            parent = self._rng.choice([e[0] for e in self._elite])

        n_params = self._rng.randint(MUTATION_PARAM_MIN, MUTATION_PARAM_MAX)
        candidates = list(PARAM_SPACES.keys())
        to_mutate = self._rng.sample(candidates, n_params)

        child = copy.deepcopy(parent)
        for key in to_mutate:
            space = PARAM_SPACES[key]
            current = child.get(key, space.min)
            step = space.step
            delta = self._rng.choice([-1, 1]) * step
            new_val = current + delta
            new_val = max(space.min, min(space.max, new_val))
            new_val = round(new_val / step) * step
            child[key] = new_val

        self._restart_counter += 1
        return child

    def _random_config(self) -> dict:
        cfg = {}
        for key, space in PARAM_SPACES.items():
            raw = self._rng.uniform(space.min, space.max)
            step = space.step
            val = round(raw / step) * step
            val = max(space.min, min(space.max, val))
            cfg[key] = val
        self._restart_counter = 0
        return cfg

    def _cfg_key(self, config: dict) -> str:
        items = sorted(config.items())
        return str(items)

    def update(self, result: FitnessResult) -> bool:
        cfg = copy.deepcopy(result.config)
        fitness = result.fitness
        std = result.fitness_std

        self._fitness_std[self._cfg_key(cfg)] = std
        self._all_evaluated.append((cfg, fitness))

        if fitness > self._best_fitness:
            self._best_fitness = fitness
            self._best_config = copy.deepcopy(cfg)
            improved = True
        else:
            improved = False

        self._stagnation = 0 if improved else self._stagnation + 1

        self._elite.append((copy.deepcopy(cfg), fitness))
        self._elite.sort(key=lambda x: x[1], reverse=True)
        self._elite = self._elite[:ELITE_SIZE]

        return improved

    @property
    def best_config(self) -> dict:
        return copy.deepcopy(self._best_config)

    @property
    def best_fitness(self) -> float:
        return self._best_fitness

    @property
    def stagnation(self) -> int:
        return self._stagnation

    @property
    def elite_configs(self) -> list[tuple[dict, float]]:
        return list(self._elite)