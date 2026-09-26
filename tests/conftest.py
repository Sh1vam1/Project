import numpy as np
import pytest
from PIL import Image

from vivaah.color import linear_to_srgb, srgb_to_linear


def synthetic_scene(width: int = 480, height: int = 320, seed: int = 0) -> np.ndarray:
    """A small linear-light test scene: sky, foliage, skin, red outfit, gold, white, shadows."""
    rng = np.random.default_rng(seed)
    enc = np.zeros((height, width, 3), np.float32)
    enc[: height // 3] = np.array([0.62, 0.72, 0.86])  # sky
    enc[height // 3: 2 * height // 3] = np.array([0.33, 0.47, 0.20])  # foliage
    enc[2 * height // 3:] = np.array([0.45, 0.40, 0.36])  # ground
    enc[height // 3: height - 20, 40:130] = np.array([0.80, 0.58, 0.45])  # skin
    enc[height // 3: height - 20, 150:230] = np.array([0.66, 0.08, 0.14])  # red lehenga
    enc[height // 3: height - 20, 250:320] = np.array([0.83, 0.68, 0.22])  # gold
    enc[height // 3: height - 20, 340:410] = np.array([0.94, 0.93, 0.90])  # white dress
    enc[height - 40:, 420:] = 0.03  # deep shadow
    enc += rng.normal(0, 0.01, enc.shape).astype(np.float32)
    return srgb_to_linear(np.clip(enc, 0, 1))


@pytest.fixture
def scene() -> np.ndarray:
    return synthetic_scene()


def save_jpeg(rgb_linear: np.ndarray, path) -> None:
    enc = (np.clip(linear_to_srgb(np.clip(rgb_linear, 0, 1)), 0, 1) * 255 + 0.5).astype(np.uint8)
    Image.fromarray(enc).save(path, quality=95)


@pytest.fixture
def jpeg_folder(tmp_path, scene):
    folder = tmp_path / "wedding"
    folder.mkdir()
    save_jpeg(scene, folder / "a_day.jpg")
    save_jpeg(scene * 0.35, folder / "b_dark.jpg")
    warm = scene * np.array([1.0, 0.82, 0.58], np.float32)
    save_jpeg(warm, folder / "c_warm.jpg")
    return folder
