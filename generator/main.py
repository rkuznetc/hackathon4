"""CLI точка входа генератора синтетических CSV (offline, не backend)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from generator import generate_all


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Генерация synthetic CSV для импорта в toll-roads backend."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data"),
        help="Каталог для CSV и JSON (по умолчанию «data» относительно текущего cwd)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Опционально переопределить seed RNG (по умолчанию из generator.py)",
    )
    args = parser.parse_args()
    out = args.output_dir
    if not out.is_absolute():
        out = (Path.cwd() / out).resolve()
    kwargs: dict = {}
    if args.seed is not None:
        kwargs["seed"] = args.seed
    summary = generate_all(output_dir=out, **kwargs)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
