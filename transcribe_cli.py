"""Transcribe an audio file with Underdog Bark (regular, clean, or both)."""

import argparse
import sys

import bark_engine


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio")
    parser.add_argument("--variant", default="both", choices=("regular", "clean", "both"))
    args = parser.parse_args(argv)
    variants = bark_engine.VARIANTS if args.variant == "both" else (args.variant,)
    try:
        for variant in variants:
            result = bark_engine.transcribe_file(args.audio, variant)
            print(
                f"[{result['variant']}] load {result['load_seconds']:.2f}s  "
                f"transcribe {result['seconds']:.2f}s  (audio {result['audio_seconds']:.2f}s)"
            )
            print(result["text"])
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
