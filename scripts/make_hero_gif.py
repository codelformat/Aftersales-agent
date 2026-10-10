"""Build the hero GIF from captured replay frames.

UPDATE_MEDIA=1 npm --prefix web run e2e -- media.spec.ts
uv run --with pillow python scripts/make_hero_gif.py
"""

from pathlib import Path
import sys

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def build_gif(input_dir, output):
    paths = sorted(Path(input_dir).glob("frame-*.png"))
    if not paths:
        raise ValueError("No hero frames; run the media capture first")
    frames, durations = [], []
    previous = None
    for path in paths:
        with Image.open(path) as source:
            frame = source.convert("RGB").resize(
                (960, round(source.height * 960 / source.width)), Image.Resampling.LANCZOS,
            )
        pixels = frame.tobytes()
        if pixels == previous:
            durations[-1] += 400
        else:
            frames.append(frame)
            durations.append(400)
            previous = pixels

    # Sample across the whole replay for one stable palette.
    samples = frames[::max(1, len(frames) // 24)]
    atlas = Image.new("RGB", (160, 100 * len(samples)))
    for index, frame in enumerate(samples):
        thumbnail = frame.copy()
        thumbnail.thumbnail((160, 100), Image.Resampling.LANCZOS)
        atlas.paste(thumbnail, (0, index * 100))
    palette = atlas.quantize(colors=128)
    quantized = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    quantized[0].save(
        output, save_all=True, append_images=quantized[1:], loop=0,
        optimize=True, duration=durations,
    )
    return len(frames)


def main():
    output = ROOT / "docs/media/hero.gif"
    try:
        count = build_gif(ROOT / "web/test-results/hero-frames", output)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    size = output.stat().st_size
    print(f"{count} frames, {size:,} bytes ({size / 1_000_000:.2f} MB)")
    if size > 5_000_000:
        print("Hero GIF exceeds 5 MB", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
