"""Read/write/apply config.py with snapshot and restore."""

import re
import py_compile
import importlib
import shutil
from pathlib import Path

from shokti.core.config import ROOT_DIR

CONFIG_FILE_PATH = ROOT_DIR / "shokti" / "core" / "config.py"


def read_current_config() -> dict:
    """Read the 9 evolvable params from shokti.core.config."""
    from shokti.core.config import MCQ, SAMPLING
    return {
        "WEAK_THRESHOLD":    MCQ.WEAK_THRESHOLD,
        "QBANK_RATIO":       MCQ.QBANK_RATIO,
        "GENERATED_RATIO":   MCQ.GENERATED_RATIO,
        "WEAK_TOPIC_RATIO":  MCQ.WEAK_TOPIC_RATIO,
        "GAP_THRESHOLD":     MCQ.GAP_THRESHOLD,
        "WEAKNESS_WEIGHT":   SAMPLING.WEAKNESS_WEIGHT,
        "DEBT_WEIGHT":       SAMPLING.DEBT_WEIGHT,
        "IMPORTANCE_WEIGHT": SAMPLING.IMPORTANCE_WEIGHT,
        "SM2_INITIAL_EF":    MCQ.SM2_INITIAL_EF,
    }


def snapshot_config() -> str:
    return CONFIG_FILE_PATH.read_text()


def restore(snapshot: str) -> bool:
    CONFIG_FILE_PATH.write_text(snapshot)
    importlib.invalidate_caches()
    return True


def _normalize_value(value, key):
    """Quantize to step and format as clean decimal."""
    from shokti.evolution.config import PARAM_SPACES
    if isinstance(value, float):
        space = PARAM_SPACES.get(key)
        step = space.step if space else 0.01
        # Quantize via integer math to avoid float corruption
        scaled = round(value / step)
        if step == int(step):
            return repr(int(scaled))
        # Use string formatting to avoid IEEE 754 noise
        precision = len(str(step).split(".")[1])
        return f"{scaled * step:.{precision}f}"
    return repr(value)


def apply(config: dict) -> bool:
    text = CONFIG_FILE_PATH.read_text()
    original = text

    for key, value in config.items():
        display = _normalize_value(value, key)
        # Try "key: type = VALUE" first (with type annotation)
        m = re.search(
            rf'^(\s*{re.escape(key)}:\s*\w+\s*=)\s*.*$',
            text, re.MULTILINE
        )
        if m:
            text = text[:m.start()] + m.group(1) + " " + display + text[m.end():]
        else:
            # Fall back to "key: VALUE" (bare, no annotation)
            m = re.search(
                rf'^(\s*{re.escape(key)}:\s*)[^=]*$',
                text, re.MULTILINE
            )
            if m:
                text = text[:m.start()] + m.group(1) + display + text[m.end():]
            else:
                return False

    try:
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode='w') as tmp:
            tmp.write(text)
            tmp_path = tmp.name
        try:
            py_compile.compile(tmp_path, doraise=True)
        finally:
            os.unlink(tmp_path)
    except py_compile.PyCompileError:
        CONFIG_FILE_PATH.write_text(original)
        return False

    CONFIG_FILE_PATH.write_text(text)
    importlib.invalidate_caches()
    return True


def deploy() -> bool:
    from shokti.evolution.models import get_best_config, create_evolution_table
    from shokti.core.config import DB_PATH
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    create_evolution_table(conn)
    cfg = get_best_config(conn)
    conn.close()
    if cfg is None:
        return False
    return apply(cfg)