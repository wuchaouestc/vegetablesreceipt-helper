"""Enhancement intentionally deferred by section 6 of the user's plan."""
from dataclasses import dataclass
from pathlib import Path
from PIL import Image
from .storage import digest


@dataclass(frozen=True)
class ImageVariant:
    path: Path
    sha256: str
    size: tuple
    mime: str
    preprocessing: str = "disabled_by_plan"
    transform: tuple = (1, 0, 0, 0, 1, 0, 0, 0, 1)


def original_image(path, *, enhancement=False):
    if enhancement:
        raise ValueError("图片增强按方案第六节暂缓，不能静默启用")
    path = Path(path).resolve()
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        return ImageVariant(path, digest(path), image.size, Image.MIME.get(image.format, "application/octet-stream"))
