"""Cut each bird out of its background photo, with a manual-override folder."""
import numpy as np
from PIL import Image
from rembg import new_session, remove
from scipy import ndimage

_SESSION = None

# Photos with soft/blurred backgrounds (a common look in bird photography)
# can leave a faint translucent halo after matting - snap low-alpha pixels
# to fully transparent so cutouts have a clean edge instead of a smudgy one.
ALPHA_CLEAN_THRESHOLD = 40


def _session():
    global _SESSION
    if _SESSION is None:
        _SESSION = new_session("isnet-general-use")
    return _SESSION


def _clean_alpha(img, threshold=ALPHA_CLEAN_THRESHOLD):
    """Snap soft edges to fully transparent, then keep only the main bird
    blob. Segmentation occasionally tags debris as foreground too - a rock
    sitting apart from the bird, or a blurred branch/splash thinly connected
    to it. Eroding first severs thin bridges to debris so labeling can find
    the bird's own blob cleanly; dilating back out then recovers the bird's
    true edges (a straight largest-connected-component pass alone can't
    separate the flycatcher-tail-plus-attached-smudge case, since they're
    one connected region until the bridge between them is broken)."""
    r, g, b, a = img.split()
    a_arr = np.array(a)
    mask = a_arr >= threshold

    iterations = max(2, round(min(img.width, img.height) / 250))
    eroded = ndimage.binary_erosion(mask, iterations=iterations)
    labeled, num = ndimage.label(eroded)
    if num > 0:
        sizes = ndimage.sum(eroded, labeled, range(1, num + 1))
        seed = labeled == (int(np.argmax(sizes)) + 1)
        recovered = ndimage.binary_dilation(seed, iterations=iterations)
        mask = mask & recovered

    new_a = np.where(mask, a_arr, 0).astype("uint8")
    return Image.merge("RGBA", (r, g, b, Image.fromarray(new_a, mode="L")))


def get_cutout(code, photo_path, cutouts_dir, manual_dir, log=print):
    manual_path = manual_dir / f"{code}.png"
    if manual_path.exists():
        return manual_path

    dest = cutouts_dir / f"{code}.png"
    if dest.exists():
        return dest

    if photo_path is None:
        return None

    try:
        img = Image.open(photo_path).convert("RGB")
        out = remove(img, session=_session())
        out = _clean_alpha(out)
        out.save(dest)
    except Exception as e:
        log(f"  ! background removal failed for {code}: {e}")
        return None
    return dest
