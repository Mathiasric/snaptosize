import io
import os
import tempfile
import zipfile
from pathlib import Path
from datetime import datetime

from PIL import Image, ImageOps
import gradio as gr


# --- legg under constants / config ---
HERE = Path(__file__).resolve().parent
CSS_PATH = HERE / "theme_clean_2.css"
CUSTOM_CSS = CSS_PATH.read_text(encoding="utf-8") if CSS_PATH.exists() else ""

print("CSS loaded:", len(CUSTOM_CSS), "from", CSS_PATH)

# ---------------------------------------------------------
# Constants
# ---------------------------------------------------------
JPEG_QUALITY = 80
DPI = (300, 300)

# Etsy constraints
MAX_ZIP_SIZE_MB = 20
MAX_ZIP_SIZE_BYTES = MAX_ZIP_SIZE_MB * 1024 * 1024

APP_NAME = "SnapToSize"
PPI = 300  # 300 DPI/PPI export

# ---------------------------------------------------------
# Utilities
# ---------------------------------------------------------
def normalize_image(im: Image.Image) -> Image.Image:
    """Fix EXIF rotation + ensure RGB."""
    im = ImageOps.exif_transpose(im)
    if im.mode != "RGB":
        im = im.convert("RGB")
    return im


def resize_image(im: Image.Image, w: int, h: int) -> Image.Image:
    """High-quality LANCZOS resize (stretch to exact WxH)."""
    return im.resize((w, h), Image.LANCZOS)


def safe_name(s: str) -> str:
    """Safe filename stub."""
    return (
        s.replace(" ", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "")
        .replace("(", "")
        .replace(")", "")
        .replace(",", "")
    )


def ensure_under_etsy_limit(file_path: str):
    """Hard fail if any ZIP exceeds Etsy's 20MB/file cap."""
    size = os.path.getsize(file_path)
    if size > MAX_ZIP_SIZE_BYTES:
        mb = size / (1024 * 1024)
        raise gr.Error(
            f"ZIP is too large for Etsy upload ({mb:.1f}MB > {MAX_ZIP_SIZE_MB}MB).\n\n"
            "Fix options:\n"
            "• Remove some size groups (generate fewer ZIPs)\n"
            "• Lower JPEG quality (we can add a slider next)\n"
            "• Some images compress worse (high noise/detail) — try a cleaner source"
        )


def make_run_dir() -> Path:
    """Create a per-run temp directory (safe for web hosting)."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(tempfile.mkdtemp(prefix=f"snaptosize_{ts}_"))


# ---------------------------------------------------------
# Presets (single source of truth)
# ---------------------------------------------------------
PRINT_SIZES = {
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

GROUP_ORDER = ["2x3", "3x4", "4x5", "ISO", "EXTRAS"]


# ---------------------------------------------------------
# Size choice builder (used by Single Export + can be reused later)
# ---------------------------------------------------------
def _inch_to_px(x_in: float) -> int:
    return int(round(x_in * PPI))


def build_size_map(group: str, orientation: str):
    """
    Returns:
      choices: list[str] dropdown labels
      lookup: dict[label] = (w_px, h_px, base_label_for_filename)

    Orientation:
      - "Portrait": use as-is
      - "Landscape": swap w/h (including ISO)  <-- user chose A
    """
    orientation = (orientation or "").strip()
    if group not in PRINT_SIZES:
        return [], {}

    def fmt_in(x):
        # Pretty inch formatting: 8.0 -> "8", 8.5 -> "8.5"
        try:
            xf = float(x)
        except Exception:
            return str(x)
        if abs(xf - round(xf)) < 1e-9:
            return str(int(round(xf)))
        s = f"{xf}".rstrip("0").rstrip(".")
        return s

    choices = []
    lookup = {}

    for spec in PRINT_SIZES[group]:
        # ISO: ("A4", 2480, 3508)
        if group == "ISO":
            label, w_px, h_px = spec

            if orientation == "Landscape":
                w_px, h_px = h_px, w_px

            pretty = f"{label} ({w_px}×{h_px})"
            choices.append(pretty)
            lookup[pretty] = (int(w_px), int(h_px), label)
            continue

        # Inch-based: (w_in, h_in) OR ("label", w_in, h_in)
        if isinstance(spec, tuple) and len(spec) == 3:
            _label, w_in, h_in = spec
        else:
            w_in, h_in = spec

        # Build label that matches orientation
        if orientation == "Landscape":
            size_label = f"{fmt_in(h_in)}x{fmt_in(w_in)}"
        else:
            size_label = f"{fmt_in(w_in)}x{fmt_in(h_in)}"

        # Convert inches to px (portrait base), then swap pixels if landscape
        w_px = _inch_to_px(float(w_in))
        h_px = _inch_to_px(float(h_in))

        if orientation == "Landscape":
            w_px, h_px = h_px, w_px

        pretty = f"{size_label} in ({w_px}×{h_px})"
        choices.append(pretty)
        lookup[pretty] = (int(w_px), int(h_px), size_label)

    return choices, lookup



# ---------------------------------------------------------
# Batch ZIP generator (v1 product behavior)
# Stretch-only (preserve all artwork). No crop.
# One ZIP per group. Etsy 20MB cap enforced.
# Server-safe temp output.
# ---------------------------------------------------------
def generate_zip(image_path, groups):
    if not image_path:
        raise gr.Error("Upload an image first.")
    if not groups:
        raise gr.Error("Choose at least one group.")

    im = normalize_image(Image.open(image_path))

    run_dir = make_run_dir()
    result_files = []

    for group in groups:
        out = io.BytesIO()

        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for spec in PRINT_SIZES[group]:
                if group == "ISO":
                    label, w, h = spec
                else:
                    if isinstance(spec, tuple) and len(spec) == 3:
                        label, w_in, h_in = spec
                    else:
                        w_in, h_in = spec
                        label = f"{w_in}x{h_in}in"

                    w = int(round(float(w_in) * PPI))
                    h = int(round(float(h_in) * PPI))

                img = resize_image(im, w, h)
                filename = f"{safe_name(label)}_{w}x{h}.jpg"

                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=JPEG_QUALITY, dpi=DPI)
                buf.seek(0)
                zf.writestr(filename, buf.read())

        out.seek(0)
        zip_path = run_dir / f"{group}.zip"
        zip_path.write_bytes(out.getvalue())

        ensure_under_etsy_limit(str(zip_path))
        result_files.append(str(zip_path))

    return result_files


# ---------------------------------------------------------
# Single size export (Advanced)
# Stretch-only, driven by PRINT_SIZES
# ---------------------------------------------------------
def single_export(image_pil, orientation, group, size_choice):
    if image_pil is None:
        raise gr.Error("Upload an image first.")
    if not group:
        raise gr.Error("Choose a group.")
    if not size_choice:
        raise gr.Error("Choose a size.")

    choices, lookup = build_size_map(group, orientation)
    if size_choice not in lookup:
        raise gr.Error("Invalid size selection. Try selecting the group again.")

    w_px, h_px, base_label = lookup[size_choice]

    im = normalize_image(image_pil)
    out_img = resize_image(im, w_px, h_px)

    run_dir = make_run_dir()
    fname = f"export_{safe_name(group)}_{safe_name(base_label)}_{w_px}x{h_px}.jpg"
    out_path = run_dir / fname

    out_img.save(str(out_path), "JPEG", quality=JPEG_QUALITY, dpi=DPI)
    return str(out_path)


def update_single_size_choices(orientation, group):
    choices, _ = build_size_map(group, orientation)
    return gr.update(choices=choices, value=(choices[0] if choices else None))


# ---------------------------------------------------------
# Checkbox helpers
# ---------------------------------------------------------
def select_all_groups():
    return list(PRINT_SIZES.keys())


def clear_all_groups():
    return []


# ---------------------------------------------------------
# UI
# ---------------------------------------------------------
with gr.Blocks(title=APP_NAME, css=CUSTOM_CSS, elem_id="app-root") as app:


    gr.Markdown(
        """
# **SnapToSize**

Fast, clean, high-quality print preparation — without the guesswork.

• **Batch ZIP** → generate Etsy-ready print-size ZIPs from a single image  
• **Single Size Export (Advanced)** → export one specific size from the same presets  
• **Output**: JPEG, quality 80, 300 DPI  
• Supports: **JPG, PNG, WEBP**
        """,
        elem_id="hero-text",
    )

    # ==================== BATCH ZIP ====================
    with gr.Tab("Batch ZIP", elem_id="tab-batch"):
        gr.Markdown(
            "Create a complete print set from **one image** and download it as multiple ZIPs (one per group).",
            elem_classes=["tab-description"],
        )
        gr.Markdown(
            "✅ **Smart Resize (default):** preserves all artwork. Minor proportional scaling may occur to match print ratios.",
            elem_classes=["tab-description"],
        )
        gr.Markdown(
            f"⚠️ Etsy limit: **{MAX_ZIP_SIZE_MB}MB per ZIP** and **max 5 files** per listing.",
            elem_classes=["tab-description"],
        )

        with gr.Row(elem_id="batch-row"):
            input_img = gr.Image(
                type="filepath",
                label="Upload image",
                elem_id="batch-input-image",
            )
            output_zip = gr.Files(
                label="Download ZIPs",
                elem_id="batch-output-zip",
            )

        group_select = gr.CheckboxGroup(
            GROUP_ORDER,
            label="Select print groups",
            elem_id="batch-group-select",
        )

        with gr.Row(elem_id="batch-actions-row"):
            gr.Button("Select all groups", elem_classes=["secondary"]).click(
                select_all_groups, [], group_select
            )
            gr.Button("Clear selection", elem_classes=["secondary"]).click(
                clear_all_groups, [], group_select
            )

        gr.Button("Generate ZIPs", elem_id="batch-generate-btn").click(
            generate_zip, [input_img, group_select], output_zip
        )

    # ==================== SINGLE EXPORT (ADVANCED) ====================
    with gr.Tab("Single Size Export (Advanced)", elem_id="tab-single-export"):

        gr.Markdown(
            "## Single Size Export (Advanced)\n"
            "_Export one specific print size from the same presets used in Batch ZIP._",
            elem_id="single-export-header",
        )

        gr.Markdown(
            "✅ Uses **Smart Resize** (no cropping). Choose **Landscape** to swap dimensions (including ISO).",
            elem_classes=["tab-description"],
        )

        with gr.Row(elem_id="single-row"):
            single_img = gr.Image(
                type="pil",
                label="Upload image",
                elem_id="single-input-image",
            )
            single_out = gr.File(
                label="Download JPG",
                elem_id="single-output-file",
            )

        with gr.Row(elem_id="single-controls-row"):
            orientation = gr.Radio(
                ["Portrait", "Landscape"],
                value="Portrait",
                label="Orientation",
                elem_id="single-orientation",
            )

            single_group = gr.Dropdown(
                GROUP_ORDER,
                value="4x5",
                label="Ratio family",
                elem_id="single-group",
            )

            initial_choices, _ = build_size_map("4x5", "Portrait")
            single_size = gr.Dropdown(
                initial_choices,
                value=(initial_choices[0] if initial_choices else None),
                label="Size",
                elem_id="single-size",
            )

        # MUST be inside Blocks/Tab context
        orientation.change(
            update_single_size_choices,
            inputs=[orientation, single_group],
            outputs=single_size,
        )
        single_group.change(
            update_single_size_choices,
            inputs=[orientation, single_group],
            outputs=single_size,
        )

        gr.Button("Export JPG", elem_id="single-export-btn").click(
            single_export,
            inputs=[single_img, orientation, single_group, single_size],
            outputs=single_out,
        )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "7860"))
    host = "0.0.0.0" if os.getenv("SPACE_ID") else "127.0.0.1"
    app.launch(server_name=host, server_port=port)
