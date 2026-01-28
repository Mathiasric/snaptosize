import io
import os
import zipfile
from pathlib import Path
from datetime import datetime

from PIL import Image, ImageOps
from tqdm import tqdm

# ---------------------------------------------------------
# Paths
# ---------------------------------------------------------
base_dir = Path(__file__).resolve().parent.parent
input_dir = base_dir / "input"
timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
output_dir = base_dir / "output" / timestamp
output_dir.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------
# Constants
# ---------------------------------------------------------
JPEG_QUALITY = 80
DPI = (300, 300)
MAX_ZIP_SIZE_MB = 20

# ---------------------------------------------------------
# Print ratios (same as webapp batch ZIP)
# Stretch logic → no cropping
# ---------------------------------------------------------
RATIOS = {
    "2x3": [(4, 6), (8, 12), (10, 15), (12, 18), (16, 24), (20, 30)],
    "3x4": [(6, 8), (9, 12), (12, 16), (15, 20), (18, 24)],
    "4x5": [(8, 10), (12, 15), (16, 20), (20, 25)],
    "ISO": [
        ("A5", 1748, 2480),
        ("A4", 2480, 3508),
        ("A3", 3508, 4961),
        ("A2", 4961, 7016),
        ("A1", 7016, 9933),
    ],
    "EXTRAS": [
        ("5x7", 5, 7),
        ("8.5x11", 8.5, 11),
        ("11x14", 11, 14),
        ("16x20", 16, 20),
        ("20x24", 20, 24),
    ],
}

# ---------------------------------------------------------
# Utilities
# ---------------------------------------------------------
def normalize_image(im: Image.Image) -> Image.Image:
    """Auto-rotate via EXIF + ensure RGB."""
    im = ImageOps.exif_transpose(im)

    # Explicit HEIC rejection (no pillow-heif installed)
    if getattr(im, "format", None) == "HEIC":
        raise RuntimeError(
            "HEIC-format støttes ikke i denne versjonen. "
            "Konverter bildet til JPG eller PNG."
        )

    if im.mode != "RGB":
        im = im.convert("RGB")
    return im

def safe_name(s: str) -> str:
    return (
        s.replace(" ", "_")
         .replace("/", "_")
         .replace("\\", "_")
         .replace("(", "")
         .replace(")", "")
         .replace(":", "")
         .replace(",", "")
    )

def resize_stretch(im: Image.Image, w: int, h: int) -> Image.Image:
    """Batch ZIP = stretch only (same logic as webapp)."""
    return im.resize((w, h), Image.LANCZOS)

# ---------------------------------------------------------
# ZIP handling
# ---------------------------------------------------------
def split_zip(zip_path, max_mb=20):
    """Split ZIP into parts if it exceeds max_mb."""
    max_size = max_mb * 1024 * 1024
    if os.path.getsize(zip_path) <= max_size:
        return

    print(f"⚠️ Splitting {zip_path.name} (over {max_mb}MB)")

    with zipfile.ZipFile(zip_path, "r") as zf:
        files = zf.infolist()
        chunks = []
        current_chunk = []
        current_size = 0

        for f in files:
            data = zf.read(f)
            if current_size + len(data) > max_size and current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
                current_size = 0

            current_chunk.append((f.filename, data))
            current_size += len(data)

        if current_chunk:
            chunks.append(current_chunk)

    # Write split ZIPs
    for i, chunk in enumerate(chunks, start=1):
        part_name = zip_path.with_name(f"{zip_path.stem}_part{i}.zip")
        with zipfile.ZipFile(part_name, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in chunk:
                zf.writestr(name, data)
        print(f"🧩 Saved: {part_name.name}")

    zip_path.unlink()  # remove original

# ---------------------------------------------------------
# Print set generator
# ---------------------------------------------------------
def generate_print_zip(image_path: Path):
    """Create one ZIP per input image containing all print sizes."""
    im = Image.open(image_path)

    # If HEIC: stop early (no pillow-heif support)
    if im.format == "HEIC":
        raise RuntimeError(
            f"HEIC ikke støttet for: {image_path.name}. "
            "Konverter til JPG eller PNG."
        )

    im = normalize_image(im)

    zip_name = output_dir / f"{safe_name(image_path.stem)}_prints.zip"

    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:
        print(f"\n🖼 Processing {image_path.name} → generating print set")

        for ratio, sizes in RATIOS.items():

            # ISO uses px directly
            if ratio == "ISO":
                for label, w_px, h_px in tqdm(sizes, desc=f"{ratio} sizes"):
                    out_img = resize_stretch(im, w_px, h_px)
                    fname = f"{label}_{w_px}x{h_px}.jpg"

                    buf = io.BytesIO()
                    out_img.save(buf, "JPEG", quality=JPEG_QUALITY, dpi=DPI)
                    buf.seek(0)
                    zf.writestr(fname, buf.read())

            else:
                for spec in tqdm(sizes, desc=f"{ratio} sizes"):
                    if isinstance(spec, tuple) and len(spec) == 3:
                        label, w_in, h_in = spec
                    else:
                        w_in, h_in = spec
                        label = f"{w_in}x{h_in}in"

                    w_px = int(round(w_in * 300))
                    h_px = int(round(h_in * 300))

                    out_img = resize_stretch(im, w_px, h_px)
                    fname = f"{safe_name(label)}_{w_px}x{h_px}.jpg"

                    buf = io.BytesIO()
                    out_img.save(buf, "JPEG", quality=JPEG_QUALITY, dpi=DPI)
                    buf.seek(0)
                    zf.writestr(fname, buf.read())

    print(f"📦 Saved ZIP → {zip_name.name}")
    split_zip(zip_name, MAX_ZIP_SIZE_MB)

# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
def main():
    if not input_dir.exists():
        print(f"❌ Input folder missing: {input_dir}")
        return

    files = list(input_dir.glob("*.*"))
    if not files:
        print("❌ No images found in /input")
        return

    for file in files:
        try:
            generate_print_zip(file)
        except Exception as e:
            print(f"❌ Error processing {file.name}: {e}")

if __name__ == "__main__":
    main()
