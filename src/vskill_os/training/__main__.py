"""Train, resume, or evaluate the standalone CPU residual-policy baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import DomainRandomization, TrainingConfig
from .learning import evaluate, load_checkpoint, train, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    trainer = subcommands.add_parser("train", help="additional CEM training generations")
    trainer.add_argument("--config", type=Path)
    trainer.add_argument("--output", type=Path, default=Path("artifacts/standing-training"))
    trainer.add_argument("--iterations", type=int, default=3)
    trainer.add_argument("--resume", type=Path)
    trainer.add_argument("--adapter", help="synthetic or module:factory; never auto-launches Isaac")
    evaluator = subcommands.add_parser("evaluate", help="held-out seeds across all four stages")
    evaluator.add_argument("--checkpoint", type=Path, required=True)
    evaluator.add_argument("--episodes", type=int, default=8, help="episodes per curriculum stage")
    evaluator.add_argument("--text", help="optional printable custom text instead of curriculum")
    evaluator.add_argument("--output", type=Path, default=Path("artifacts/standing-training/evaluation.json"))
    args = parser.parse_args()
    try:
        if args.command == "train":
            content = {}
            if args.config:
                content = json.loads(args.config.read_text(encoding="utf-8"))
            elif args.resume:
                previous = load_checkpoint(args.resume)
                content = {"training": previous["config"], "randomization": previous["randomization"]}
            unknown = set(content) - {"training", "randomization", "description"}
            if unknown:
                raise ValueError(f"unknown configuration sections: {sorted(unknown)}")
            training = dict(content.get("training", {}))
            if args.adapter:
                training["adapter"] = args.adapter
            config = TrainingConfig(**training)
            randomization = DomainRandomization(**content.get("randomization", {}))
            checkpoint = train(config, randomization, output=args.output,
                               iterations=args.iterations, resume=args.resume)
            summary = {"checkpoint": str(args.output / "checkpoint.json"),
                       "iteration": checkpoint["iteration"], "next_stage": checkpoint["stage"],
                       "evidence_kind": checkpoint["evidence_kind"],
                       "last_validation": checkpoint["last_validation"],
                       "physical_acceptance": False}
        else:
            result = evaluate(load_checkpoint(args.checkpoint), episodes_per_stage=args.episodes, text=args.text)
            write_json(args.output, result)
            summary = {"report": str(args.output), **result["summary"], "note": result["note"]}
    except (ValueError, TypeError, KeyError, OSError, ImportError, AttributeError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
