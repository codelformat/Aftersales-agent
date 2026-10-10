"""Check hero GIF resizing, shared palette and merged frame timing.

Usage: uv run --with pillow python scripts/test_make_hero_gif.py
"""

from pathlib import Path
import tempfile
import unittest

from PIL import Image

from make_hero_gif import build_gif


class HeroGifTests(unittest.TestCase):
    def test_merges_identical_frames_and_keeps_end_hold(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames = root / "frames"
            frames.mkdir()
            for index, color in enumerate(["red", "red", "blue", "blue", "blue"]):
                Image.new("RGB", (1280, 800), color).save(frames / f"frame-{index:04d}.png")
            output = root / "hero.gif"
            self.assertEqual(build_gif(frames, output), 2)
            with Image.open(output) as gif:
                self.assertEqual(gif.size, (960, 600))
                self.assertEqual(gif.n_frames, 2)
                self.assertEqual(gif.info["loop"], 0)
                self.assertEqual(gif.info["duration"], 800)
                gif.seek(1)
                self.assertEqual(gif.info["duration"], 1200)
                self.assertEqual(gif.convert("RGB").getpixel((0, 0)), (0, 0, 255))

    def test_rejects_missing_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "No hero frames"):
                build_gif(root, root / "hero.gif")


if __name__ == "__main__":
    unittest.main()
