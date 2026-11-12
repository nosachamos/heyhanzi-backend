"""Public offline quality-check command for recordings and regression probes."""

import argparse
import json
from pathlib import Path

import soundfile as sf
import torch

from scripts.audio_pipeline.quality import Quality


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--pinyin", help="Intended curricular reading, when available")
    parser.add_argument("--asr", type=Path, required=True)
    parser.add_argument("--phonetic", type=Path)
    parser.add_argument("--device", default="cuda:0", help="ASR device (use cpu for CPU-only audits)")
    parser.add_argument("--completion", choices=["eos", "limit", "interrupted"], required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(2)
    samples, sr = sf.read(args.audio)
    _, report = Quality(args.asr, device=args.device, phonetic_dir=args.phonetic).assess(
        samples, sr, args.text, {"reason": args.completion}, pinyin=args.pinyin
    )
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
