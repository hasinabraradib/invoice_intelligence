"""Photo preprocessing: page edges, perspective warp, lighting, and the photo-vs-scan check.
All images are generated here with known geometry, so results can be checked exactly."""

import cv2
import numpy as np

from app.ocr import preprocess as pp

PAGE_W, PAGE_H = 700, 990  # A4-ish portrait page


def _page() -> np.ndarray:
    """A white page with dark text lines."""
    page = np.full((PAGE_H, PAGE_W), 245, np.uint8)
    for i in range(18):
        cv2.putText(
            page,
            f"Line {i}: Widget x{i} 1,234.{i:02d}",
            (40, 80 + 48 * i),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            20,
            2,
        )
    return page


def _camera_corners() -> np.ndarray:
    """Where a real camera sees the page's corners: an A4 page (21 x 29.7 cm) tilted 25
    degrees back and 15 degrees sideways, 35 cm away, through a pinhole camera (focal
    length 1100 px, principal point at the centre of a 1300 x 1400 photo)."""
    w, h = 0.21, 0.297
    page = np.array(
        [[-w / 2, -h / 2, 0], [w / 2, -h / 2, 0], [w / 2, h / 2, 0], [-w / 2, h / 2, 0]]
    )
    ax, ay = np.radians(25), np.radians(15)
    rx = np.array([[1, 0, 0], [0, np.cos(ax), -np.sin(ax)], [0, np.sin(ax), np.cos(ax)]])
    ry = np.array([[np.cos(ay), 0, np.sin(ay)], [0, 1, 0], [-np.sin(ay), 0, np.cos(ay)]])
    points = page @ (ry @ rx).T + np.array([0.0, 0.0, 0.35])
    f, u0, v0 = 1100.0, 650.0, 700.0
    return np.float32([[f * x / z + u0, f * y / z + v0] for x, y, z in points])


def _photo(gradient: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """The page photographed at an angle on a dark desk, light falling off to one side.
    Returns (image, the page's true corners TL, TR, BR, BL)."""
    corners = _camera_corners()
    src = np.float32([[0, 0], [PAGE_W, 0], [PAGE_W, PAGE_H], [0, PAGE_H]])
    matrix = cv2.getPerspectiveTransform(src, corners)
    desk = np.full((1400, 1300), 60, np.uint8)
    warped = cv2.warpPerspective(_page(), matrix, (1300, 1400), borderValue=0)
    mask = cv2.warpPerspective(np.full((PAGE_H, PAGE_W), 255, np.uint8), matrix, (1300, 1400))
    image = np.where(mask > 0, warped, desk).astype(np.float32)
    if gradient:
        xx = np.linspace(1.0, 0.6, image.shape[1])[None, :]
        image = image * xx
    rng = np.random.default_rng(1)
    image = np.clip(image + rng.normal(0, 4, image.shape), 0, 255).astype(np.uint8)
    return image, corners


def _scan() -> np.ndarray:
    """A flatbed scan: the page fills the frame, evenly lit, a little noise."""
    rng = np.random.default_rng(2)
    return np.clip(_page() + rng.normal(0, 6, (PAGE_H, PAGE_W)), 0, 255).astype(np.uint8)


def test_page_edges_are_found_in_a_photo():
    image, corners = _photo()
    quad = pp.find_document_quad(image)
    assert quad is not None
    assert np.abs(quad - corners).max() < 15  # pixels, on a 1300 x 1400 photo


def test_warp_restores_an_upright_page_of_the_right_shape():
    image, corners = _photo(gradient=False)
    flat = pp.warp_document(image, pp.find_document_quad(image))
    ratio = flat.shape[0] / flat.shape[1]
    assert abs(ratio - PAGE_H / PAGE_W) < 0.08  # the page's real shape, not the photo's
    assert abs(pp.estimate_skew_angle(flat)) < 1.0  # text lines are horizontal again
    assert np.median(flat) > 200  # it is the white page, not the desk


def test_photo_vs_scan():
    photo = pp.check_photo(_photo()[0])
    assert photo.is_photo and photo.outline is not None and photo.lighting_range >= 30
    scan = pp.check_photo(_scan())
    assert not scan.is_photo and scan.outline is None and scan.lighting_range < 30


def test_uneven_light_alone_makes_a_photo():
    """A photo where the page fills the frame (no edges) but the light falls off."""
    page = _scan().astype(np.float32) * np.linspace(1.0, 0.6, PAGE_W)[None, :]
    check = pp.check_photo(page.astype(np.uint8))
    assert check.is_photo and check.outline is None


def test_flattening_evens_out_the_paper():
    page = (_page().astype(np.float32) * np.linspace(1.0, 0.55, PAGE_W)[None, :]).astype(np.uint8)
    before = np.ptp(pp.paper_background(page))
    after = np.ptp(pp.paper_background(pp.flatten_illumination(page)))
    assert before > 80 and after < 25


def test_clahe_raises_local_contrast_of_faint_text():
    faint = (235 - (235 - _page().astype(np.float32)) * 0.3).astype(np.uint8)
    assert np.std(pp.enhance_contrast(faint)) > np.std(faint)


def test_scans_skip_the_photo_steps():
    scan = _scan()
    assert np.array_equal(
        pp.preprocess(scan, photo_steps=pp.PHOTO_STEPS), pp.preprocess(scan, photo_steps=())
    )


def test_photos_are_warped_before_ocr():
    image, _ = _photo()
    with_steps = pp.preprocess(image, photo_steps=("warp",))
    without = pp.preprocess(image, photo_steps=())
    # The warped page is portrait like the page itself; without the warp the whole photo
    # (desk included) is processed.
    assert with_steps.shape != without.shape
    assert with_steps.shape[0] > with_steps.shape[1]
