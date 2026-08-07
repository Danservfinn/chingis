"""MLX QLoRA on a <=14B target. Spec §8, §10.

Bakes logged Luna/Sol decision traces into the Phase-3 executive. Not exercised yet: this
opens after B6, and it needs mlx-lm plus a local base model, neither of which is a B1-B6
dependency. Kept minimal and honest rather than elaborate and untested.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def write_jsonl(examples: list[dict], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in examples))
    return out


def train(data: Path, model: str, adapter_out: Path, *, iters: int = 600,
          batch_size: int = 2, lora_layers: int = 8) -> subprocess.CompletedProcess:
    """Shell out to mlx_lm.lora. Requires `uv add mlx-lm` and a local base model."""
    cmd = ["python", "-m", "mlx_lm.lora", "--model", model, "--train",
           "--data", str(data.parent), "--adapter-path", str(adapter_out),
           "--iters", str(iters), "--batch-size", str(batch_size),
           "--lora-layers", str(lora_layers)]
    return subprocess.run(cmd, capture_output=True, text=True)


def available() -> tuple[bool, str]:
    try:
        import mlx_lm  # noqa: F401
        return True, "mlx-lm present"
    except ImportError:
        return False, "mlx-lm not installed (expected until the M-ladder opens, post-B6)"
