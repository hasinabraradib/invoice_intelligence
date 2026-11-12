"""Preprocessing is pure OpenCV/NumPy, so these tests need no Tesseract."""

import numpy as np
import pytest

from app.ocr import preprocess as pp
from tests import factories


def _array(page):
    return np.asarray(page)


def test_to_grayscale_handles_rgb_rgba_and_gray():
    assert pp.to_grayscale(np.zeros((10, 20, 3), np.uint8)).shape == (10, 20)
    assert pp.to_grayscale(np.zeros((10, 20, 4), np.uint8)).shape == (10, 20)
    gray = np.zeros((10, 20), np.uint8)
    assert pp.to_grayscale(gray) is gray


def test_upscale_small_images_only():
    small = np.full((300, 500), 255, np.uint8)
    assert pp.upscale_if_small(small).shape == (1200, 2000)  # 4x
    assert pp.upscale_if_small(small, dpi=150).shape == (600, 1000)  # 300/150 = 2x
    big = np.full((3500, 2480), 255, np.uint8)
    assert pp.upscale_if_small(big, dpi=72) is big  # "72 DPI" tag on a big image: ignored


@pytest.mark.parametrize("degrees", [5, -7, 12])
def test_estimate_skew_angle_recovers_rotation(degrees):
    page = factories.rotated(factories.invoice_image(), degrees)
    angle = pp.estimate_skew_angle(_array(page))
    assert abs(angle + degrees) < 1.0  # the correction is the opposite rotation


def test_skew_is_zero_for_straight_and_blank_pages():
    assert abs(pp.estimate_skew_angle(_array(factories.invoice_image()))) < 0.5
    assert pp.estimate_skew_angle(np.full((800, 600), 255, np.uint8)) == 0.0


def test_binarize_outputs_only_black_and_white():
    out = pp.binarize(_array(factories.noisy(factories.invoice_image(), sigma=20)))
    assert set(np.unique(out)) <= {0, 255}


def test_preprocess_full_pipeline_on_rgb_and_blank():
    rgb = np.asarray(factories.invoice_image().convert("RGB"))
    out = pp.preprocess(rgb)
    assert out.ndim == 2 and out.dtype == np.uint8
    blank = pp.preprocess(np.full((900, 1700), 255, np.uint8))
    assert (blank == 255).all()
