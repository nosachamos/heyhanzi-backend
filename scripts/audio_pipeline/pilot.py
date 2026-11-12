"""Generate a bounded two-mode comparison and machine-readable acceptance report."""

import argparse
import json
import logging
import subprocess
import tempfile
import time
from pathlib import Path

import soundfile as sf

from scripts.audio_pipeline.endings import verify_endings
from scripts.audio_pipeline.profiles import catalogue, profile
from scripts.audio_pipeline.punctuation import verify_punctuation
from scripts.audio_pipeline.quality import Quality, verify_encoded
from scripts.audio_pipeline.synthesis import Synthesizer


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--weights", type=Path, required=True)
    p.add_argument("--reference", type=Path, required=True)
    p.add_argument("--asr", type=Path, required=True)
    p.add_argument("--phonetic", type=Path, required=True)
    p.add_argument("--voice", choices=["female", "male"], required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--profile", type=Path)
    p.add_argument("--mode", choices=["zero_shot", "instruct2"])
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    config, _ = profile(args.voice)
    if args.profile:
        config = json.loads(args.profile.read_text())
    synth = Synthesizer(args.source, args.weights, config, args.reference)
    qa = Quality(args.asr, phonetic_dir=args.phonetic)
    logging.getLogger().setLevel(logging.ERROR)
    order, entries = catalogue()
    texts = [
        "我。",
        "你。",
        "好。",
        "我，你。",
        "你，我。",
        "这是我的车。",
        "我本来想去。",
        "街道两侧有很多商店。",
    ]
    selected = [(i, e) for i, e in entries.items() if e["text"] in texts]
    seen = {e["text"] for _, e in selected}
    for i in order:
        if len(selected) >= 20:
            break
        e = entries[i]
        if e["text"] not in seen and len(e["text"]) > 7:
            selected.append((i, e))
            seen.add(e["text"])
    selected.append(
        (
            "story-pilot",
            {
                "text": "你好！我是老师。今天我们一起学习中文。你有什么问题，可以问我。",
                "pinyin": "nǐ hǎo wǒ shì lǎoshī jīntiān wǒmen yìqǐ xuéxí zhōngwén "
                          "nǐ yǒu shénme wèntí kěyǐ wèn wǒ",
                "kind": "story-passage",
            },
        )
    )
    report = []
    for mode in [args.mode] if args.mode else ["zero_shot", "instruct2"]:
        synth.set_mode(mode)
        for i, entry in selected:
            started = time.monotonic()
            raw, termination, _, _ = synth.generate(entry, 46)
            finished, metrics = qa.assess(
                raw,
                synth.sample_rate,
                entry["text"],
                termination,
                pinyin=entry.get("pinyin") or None,
            )
            # Keep only qualified compressed examples. Lossless intermediates
            # disappear even if encoding or a delivery check raises.
            if metrics["accepted"]:
                with tempfile.TemporaryDirectory(prefix="heyhanzi-audio-pilot-") as temporary:
                    wav = Path(temporary) / "working.wav"
                    mp3 = Path(temporary) / "delivery.mp3"
                    sf.write(wav, finished, synth.sample_rate, subtype="PCM_24")
                    subprocess.run(
                        ["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-map_metadata", "-1",
                         "-codec:a", "libmp3lame", "-b:a", "96k", "-ar", "24000", "-ac", "1", str(mp3)],
                        check=True,
                    )
                    decoded, rate = sf.read(mp3, dtype="float32")
                    metrics["failures"] += verify_encoded(decoded)
                    metrics["failures"] += verify_endings(
                        decoded, rate, entry["text"], entry.get("pinyin"),
                        metrics.get("endings"),
                    )
                    metrics["failures"] += verify_punctuation(
                        decoded, rate, entry["text"], entry.get("pinyin"),
                        metrics.get("punctuation"),
                    )
                    if rate != 24000 or not 0 < mp3.stat().st_size <= 2_000_000:
                        metrics["failures"].append("delivery-format")
                    metrics["accepted"] = not metrics["failures"]
                    if metrics["accepted"]:
                        (args.output / f"{mode}-{i}.mp3").write_bytes(mp3.read_bytes())
            row = {
                "voice": args.voice,
                "mode": mode,
                "id": i,
                "text": entry["text"],
                "seconds": time.monotonic() - started,
                **metrics,
            }
            report.append(row)
            (args.output / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )
            print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
