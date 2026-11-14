"""Image cleanup before OCR. Pure functions: numpy array in, new numpy array out.

Tesseract is most accurate on sharp, straight, black-on-white text at about 300 DPI.
Phone photos and office scans are often small, noisy, slightly rotated and unevenly lit,
so each step below fixes one of those problems.
"""

from dataclasses import dataclass

import cv2
import numpy as np

TARGET_DPI = 300
# Without DPI metadata we assume a page should be at least this wide (A4 at ~250 DPI).
MIN_PAGE_WIDTH_PX = 2000
MAX_UPSCALE = 4.0
MAX_SKEW_DEGREES = 15.0


def to_grayscale(image: np.ndarray) -> np.ndarray:
    """Colour carries no information for OCR; one channel is 3x less data to process."""
    if image.ndim == 2:
        return image
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_RGBA2GRAY)
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)


def upscale_if_small(gray: np.ndarray, dpi: float | None = None) -> np.ndarray:
    """Enlarge low-resolution images: Tesseract misreads characters only a few pixels tall.

    We scale so a page is about 2000 px wide (~250-300 DPI for A4). If the image also
    states its DPI we take the smaller factor: many phone photos claim "72 DPI" while being
    4000 px wide, and trusting that tag alone would blow them up 4x for nothing.
    Never shrinks, and never enlarges more than 4x (that only magnifies blur).
    """
    factor = MIN_PAGE_WIDTH_PX / gray.shape[1]
    if dpi and dpi > 0:
        factor = min(factor, TARGET_DPI / dpi)
    factor = min(factor, MAX_UPSCALE)
    if factor <= 1.1:  # close enough; resizing would only cost time
        return gray
    return cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)


def denoise(gray: np.ndarray) -> np.ndarray:
    """Remove grain and speckles (poor scans, JPEG artefacts) while keeping letter edges.

    Non-local means averages each pixel with similar-looking patches elsewhere in the image,
    so random noise cancels out but strokes (which repeat) survive. We measured it against
    a 3x3 median filter on a noisy, rotated test invoice: mean OCR confidence 92 vs 58,
    for ~0.15 s extra per page. h=15 is the filter strength.
    """
    return cv2.fastNlMeansDenoising(gray, None, h=15, templateWindowSize=7, searchWindowSize=21)


def estimate_skew_angle(gray: np.ndarray) -> float:
    """Return the rotation (degrees) that makes the text lines horizontal.

    Method (projection profile): for each candidate angle, rotate a small copy of the
    page's text pixels and sum each pixel row. When lines are horizontal, rows alternate
    between "full of ink" and "empty", so those sums vary the most. Coarse 1-degree search,
    then 0.1-degree refinement, within +/-15 degrees.
    """
    small = _shrink(gray, max_side=1000)
    # Otsu picks the ink/paper threshold automatically; INV makes text pixels white (255).
    _, ink = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if cv2.countNonZero(ink) < 0.001 * ink.size:
        return 0.0  # blank page: nothing to straighten

    def score(angle: float) -> float:
        return float(np.var(_rotate(ink, angle, border=0).sum(axis=1, dtype=np.float64)))

    coarse = max(np.arange(-MAX_SKEW_DEGREES, MAX_SKEW_DEGREES + 0.5, 1.0), key=score)
    fine = max(np.arange(coarse - 1.0, coarse + 1.05, 0.1), key=score)
    return float(np.clip(fine, -MAX_SKEW_DEGREES, MAX_SKEW_DEGREES))


def deskew(gray: np.ndarray) -> np.ndarray:
    """Rotate the page so text lines are horizontal (Tesseract reads lines left to right)."""
    angle = estimate_skew_angle(gray)
    if abs(angle) < 0.3:  # rotating costs time and slightly blurs; skip tiny angles
        return gray
    return _rotate(gray, angle, border=255)  # fill new corners with white paper


def binarize(gray: np.ndarray) -> np.ndarray:
    """Turn the page into pure black text on pure white paper.

    Adaptive thresholding compares each pixel with its own neighbourhood (31x31 px) instead
    of one global value, so shadows and uneven lighting in photos do not swallow text.
    """
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )


# ---------------------------------------------------------------- photos
# Phone photos differ from scans: the page may not fill the frame and is seen at an angle,
# and the light falls off across it. These steps run only on images that look like photos.

PHOTO_STEPS = ("warp", "flatten", "clahe")  # every photo step there is
# Uneven lighting: the paper's brightness (text and noise smoothed away) varies by at least
# this many grey levels between the brightest and darkest parts of the page. A flatbed
# scan is far more even.
UNEVEN_LIGHT_LEVELS = 30.0


@dataclass(frozen=True)
class PhotoCheck:
    is_photo: bool
    lighting_range: float  # grey levels between bright and dark paper
    outline: np.ndarray | None  # the page's 4 corners, if the page does not fill the frame


def paper_background(gray: np.ndarray, size: int = 512) -> np.ndarray:
    """The paper's brightness without the text: a small copy, closed (text is darker than
    paper, so a large closing fills it in), then smoothed. Same size as `gray`."""
    small = _shrink(gray, max_side=size)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    closed = cv2.morphologyEx(small, cv2.MORPH_CLOSE, kernel)
    smooth = cv2.GaussianBlur(cv2.medianBlur(closed, 21), (0, 0), 9)
    return cv2.resize(smooth, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_LINEAR)


def find_document_quad(gray: np.ndarray) -> np.ndarray | None:
    """The page's four corners (top-left, top-right, bottom-right, bottom-left) in `gray`'s
    coordinates, when the page is a clear quadrilateral that does NOT fill the whole frame
    (e.g. a photo of an invoice on a desk). None otherwise."""
    small = _shrink(gray, max_side=800)
    scale = gray.shape[1] / small.shape[1]
    blurred = cv2.GaussianBlur(small, (5, 5), 0)
    edges = cv2.dilate(cv2.Canny(blurred, 50, 150), np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area = small.shape[0] * small.shape[1]
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        hull = cv2.convexHull(contour)
        approx = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
        share = cv2.contourArea(approx) / area
        if len(approx) == 4 and 0.25 < share < 0.95 and cv2.isContourConvex(approx):
            return _order_corners(approx.reshape(4, 2).astype(np.float32) * scale)
    return None


def page_aspect_ratio(quad: np.ndarray, image_shape: tuple[int, ...]) -> float | None:
    """The page's real height/width from its four corners in a photo.

    Edge lengths in the photo are foreshortened by perspective, so they give the wrong
    shape. Zhang & He ("Whiteboard scanning and image enhancement", 2007): estimate the
    camera's focal length from the corners (principal point = image centre, square
    pixels), then measure the two sides in 3D. None when the sides are nearly parallel
    (no measurable perspective): then the edge lengths are already right.
    """
    u0, v0 = image_shape[1] / 2, image_shape[0] / 2
    tl, tr, br, bl = (np.array([p[0], p[1], 1.0]) for p in quad)
    m1, m2, m3, m4 = tl, tr, bl, br
    k2 = np.dot(np.cross(m1, m4), m3) / np.dot(np.cross(m2, m4), m3)
    k3 = np.dot(np.cross(m1, m4), m2) / np.dot(np.cross(m3, m4), m2)
    n2, n3 = k2 * m2 - m1, k3 * m3 - m1
    if abs(n2[2]) < 1e-6 or abs(n3[2]) < 1e-6:
        return None
    f2 = -(
        (n2[0] * n3[0] - (n2[0] * n3[2] + n2[2] * n3[0]) * u0 + n2[2] * n3[2] * u0 * u0)
        + (n2[1] * n3[1] - (n2[1] * n3[2] + n2[2] * n3[1]) * v0 + n2[2] * n3[2] * v0 * v0)
    ) / (n2[2] * n3[2])
    if not np.isfinite(f2) or f2 <= 0:
        return None
    inv = np.linalg.inv(np.array([[np.sqrt(f2), 0, u0], [0, np.sqrt(f2), v0], [0, 0, 1]]))
    width2 = n2 @ inv.T @ inv @ n2
    height2 = n3 @ inv.T @ inv @ n3
    if width2 <= 0 or height2 <= 0:
        return None
    return float(np.sqrt(height2 / width2))


def warp_document(gray: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Perspective-correct the page inside `quad` to a flat, upright rectangle with the
    page's real proportions (see page_aspect_ratio)."""
    tl, tr, br, bl = quad
    width = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    height = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    ratio = page_aspect_ratio(quad, gray.shape)
    if ratio is not None and 0.3 < ratio < 3.5:  # sane page shapes only
        height = int(width * ratio)
    target = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], np.float32
    )
    matrix = cv2.getPerspectiveTransform(quad.astype(np.float32), target)
    return cv2.warpPerspective(gray, matrix, (width, height), flags=cv2.INTER_CUBIC)


def check_photo(gray: np.ndarray) -> PhotoCheck:
    """Does this image look like a phone photo rather than a scan? Yes if the paper is
    unevenly lit, or if a page outline sits inside the frame."""
    background = paper_background(gray)
    low, high = np.percentile(background, [2, 98])
    lighting = float(high - low)
    outline = find_document_quad(gray)
    return PhotoCheck(lighting >= UNEVEN_LIGHT_LEVELS or outline is not None, lighting, outline)


def flatten_illumination(gray: np.ndarray) -> np.ndarray:
    """Remove shadows and gradients: divide by the paper's own brightness, so the page
    becomes evenly white and text keeps its contrast everywhere."""
    background = paper_background(gray).astype(np.float32)
    flat = gray.astype(np.float32) / np.maximum(background, 1.0) * 255.0
    return np.clip(flat, 0, 255).astype(np.uint8)


def enhance_contrast(gray: np.ndarray) -> np.ndarray:
    """CLAHE: local histogram equalisation, limited so paper noise is not amplified much."""
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)


def preprocess(
    image: np.ndarray, dpi: float | None = None, photo_steps: tuple[str, ...] = PHOTO_STEPS
) -> np.ndarray:
    """Full pipeline. Order matters: shrink data first (grayscale), straighten and even out
    a photo at full resolution, fix resolution before filtering, denoise before contrast
    and skew estimation (speckles confuse both), and binarize last (rotating a black/white
    image creates jagged edges). `photo_steps` picks which photo steps may run; they only
    run on images that look like photos."""
    gray = to_grayscale(image)
    photo = check_photo(gray) if photo_steps else None
    if photo is not None and photo.is_photo:
        if "warp" in photo_steps and photo.outline is not None:
            gray = warp_document(gray, photo.outline)
        if "flatten" in photo_steps:
            gray = flatten_illumination(gray)
    gray = upscale_if_small(gray, dpi)
    gray = denoise(gray)
    if photo is not None and photo.is_photo and "clahe" in photo_steps:
        gray = enhance_contrast(gray)
    gray = deskew(gray)
    return binarize(gray)


def _order_corners(points: np.ndarray) -> np.ndarray:
    """Top-left, top-right, bottom-right, bottom-left (by coordinate sums and differences)."""
    sums, diffs = points.sum(axis=1), np.diff(points, axis=1).ravel()
    return np.array(
        [
            points[np.argmin(sums)],
            points[np.argmin(diffs)],
            points[np.argmax(sums)],
            points[np.argmax(diffs)],
        ],
        dtype=np.float32,
    )


def _shrink(gray: np.ndarray, max_side: int) -> np.ndarray:
    factor = max_side / max(gray.shape)
    if factor >= 1:
        return gray
    return cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)


def _rotate(image: np.ndarray, angle: float, border: int) -> np.ndarray:
    """Rotate around the centre, enlarging the canvas so no corner is cut off."""
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_w, new_h = int(height * sin + width * cos), int(height * cos + width * sin)
    matrix[0, 2] += new_w / 2 - width / 2
    matrix[1, 2] += new_h / 2 - height / 2
    return cv2.warpAffine(image, matrix, (new_w, new_h), flags=cv2.INTER_LINEAR, borderValue=border)
