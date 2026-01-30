import io
import os
import tempfile
import zipfile
from pathlib import Path
from datetime import datetime

import stripe
import time

from PIL import Image, ImageOps, ImageDraw, ImageFont
import gradio as gr


# ---------------------------------------------------------
# CSS
# ---------------------------------------------------------
HERE = Path(__file__).resolve().parent
CSS_PATH = HERE / "theme_clean_2.css"
CUSTOM_CSS = CSS_PATH.read_text(encoding="utf-8") if CSS_PATH.exists() else ""
print("CSS loaded:", len(CUSTOM_CSS), "from", CSS_PATH)

# ---------------------------------------------------------
# Constants
# ---------------------------------------------------------
JPEG_QUALITY = 80
DPI = (300, 300)

MAX_ZIP_SIZE_MB = 20
MAX_ZIP_SIZE_BYTES = MAX_ZIP_SIZE_MB * 1024 * 1024

APP_NAME = "SnapToSize"
PPI = 300  # 300 DPI/PPI export

# ---------------------------------------------------------
# Paywall (Stripe = source of truth)
# ---------------------------------------------------------
STRIPE_LINK = os.getenv("STRIPE_LINK", "").strip()  # your monthly payment link (for UI)
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_LINK_YEARLY = os.getenv("STRIPE_LINK_YEARLY", "").strip()

if not STRIPE_SECRET_KEY:
    raise RuntimeError("STRIPE_SECRET_KEY is not set. Add it as an environment variable.")

stripe.api_key = STRIPE_SECRET_KEY

DEMO_GROUPS = ["2x3"]
WATERMARK_TEXT = "SNAPTOSIZE DEMO"

# simple cache so we don't hit Stripe constantly
_PRO_CACHE = {}
_CACHE_TTL = 600  # seconds (10 min)

def stripe_is_pro(email: str):
    email = (email or "").strip().lower()
    if "@" not in email:
        return False, "Enter the email you used at checkout."

    now = time.time()
    cached = _PRO_CACHE.get(email)
    if cached and (now - cached["ts"]) < _CACHE_TTL:
        return cached["ok"], cached["msg"]

    # Find customers by email
    customers = stripe.Customer.list(email=email, limit=5).data
    if not customers:
        msg = "❌ No Stripe customer found for this email."
        _PRO_CACHE[email] = {"ok": False, "msg": msg, "ts": now}
        return False, msg

    # If ANY subscription is active/trialing → PRO
    for c in customers:
        subs = stripe.Subscription.list(customer=c.id, status="all", limit=20).data
        for s in subs:
            if s.status in ("active", "trialing"):
                msg = "✅ Pro unlocked (active subscription)."
                _PRO_CACHE[email] = {"ok": True, "msg": msg, "ts": now}
                return True, msg

    msg = "❌ No active subscription found."
    _PRO_CACHE[email] = {"ok": False, "msg": msg, "ts": now}
    return False, msg

def stripe_unlock_from_session(session_id: str):
    """
    Auto-unlock after Stripe redirect: ?session_id=cs_...
    Returns (ok: bool, msg: str)
    """
    session_id = (session_id or "").strip()
    if not session_id:
        return False, ""

    try:
        session = stripe.checkout.Session.retrieve(
            session_id,
            expand=["subscription", "customer", "customer_details"],
        )

        # Must be paid
        if getattr(session, "payment_status", None) != "paid":
            return False, "Payment not completed yet."

        # If subscription exists, ensure it's active/trialing
        sub = getattr(session, "subscription", None)
        if sub:
            sub_status = getattr(sub, "status", None)
            if sub_status not in ("active", "trialing"):
                return False, f"Subscription not active ({sub_status})."

        email = None
        cd = getattr(session, "customer_details", None)
        if cd and getattr(cd, "email", None):
            email = cd.email

        return True, f"✅ Pro unlocked{f' for {email}' if email else ''}."
    except Exception as e:
        return False, f"Could not verify checkout. ({type(e).__name__})"


def add_watermark(im: Image.Image, text: str = WATERMARK_TEXT) -> Image.Image:
    """
    Simple watermark that doesn't need external fonts.
    Keeps output valid JPG (RGB).
    """
    base = im.copy().convert("RGBA")
    w, h = base.size

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default()

    step = max(220, min(w, h) // 3)
    for y in range(0, h, step):
        for x in range(0, w, step):
            draw.text((x, y), text, font=font, fill=(255, 255, 255, 70))

    out = Image.alpha_composite(base, overlay).convert("RGB")
    return out


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
        str(s)
        .replace(" ", "_")
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
            "• Lower JPEG quality\n"
            "• Some images compress worse (high noise/detail)"
        )


def make_run_dir() -> Path:
    """Create a per-run temp directory (safe for web hosting)."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(tempfile.mkdtemp(prefix=f"snaptosize_{ts}_"))


# ---------------------------------------------------------
# Presets
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
# Size choice builder (for Single Export)
# ---------------------------------------------------------
def _inch_to_px(x_in: float) -> int:
    return int(round(x_in * PPI))


def build_size_map(group: str, orientation: str):
    """
    Returns:
      choices: list[str] dropdown labels
      lookup: dict[label] = (w_px, h_px, base_label_for_filename)
    """
    orientation = (orientation or "").strip()
    if group not in PRINT_SIZES:
        return [], {}

    def fmt_in(x):
        try:
            xf = float(x)
        except Exception:
            return str(x)
        if abs(xf - round(xf)) < 1e-9:
            return str(int(round(xf)))
        return f"{xf}".rstrip("0").rstrip(".")

    choices = []
    lookup = {}

    for spec in PRINT_SIZES[group]:
        if group == "ISO":
            label, w_px, h_px = spec
            if orientation == "Landscape":
                w_px, h_px = h_px, w_px
            pretty = f"{label} ({w_px}×{h_px})"
            choices.append(pretty)
            lookup[pretty] = (int(w_px), int(h_px), label)
            continue

        if isinstance(spec, tuple) and len(spec) == 3:
            _label, w_in, h_in = spec
        else:
            w_in, h_in = spec

        if orientation == "Landscape":
            size_label = f"{fmt_in(h_in)}x{fmt_in(w_in)}"
        else:
            size_label = f"{fmt_in(w_in)}x{fmt_in(h_in)}"

        w_px = _inch_to_px(float(w_in))
        h_px = _inch_to_px(float(h_in))
        if orientation == "Landscape":
            w_px, h_px = h_px, w_px

        pretty = f"{size_label} in ({w_px}×{h_px})"
        choices.append(pretty)
        lookup[pretty] = (int(w_px), int(h_px), size_label)

    return choices, lookup


# ---------------------------------------------------------
# Batch ZIP generator
# ---------------------------------------------------------
def generate_zip(image_path, groups, is_pro: bool):
    if not image_path:
        raise gr.Error("Upload an image first.")
    if not groups:
        raise gr.Error("Choose at least one group.")

    # Demo gating
    if not is_pro:
        groups = [g for g in groups if g in DEMO_GROUPS]
        if not groups:
            raise gr.Error("Demo mode: only the 2x3 group is available. Unlock Pro for full export.")

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
                        if not str(label).endswith("in"):
                            label = f"{label}in"
                    else:
                        w_in, h_in = spec
                        label = f"{w_in}x{h_in}in"

                    w = int(round(float(w_in) * PPI))
                    h = int(round(float(h_in) * PPI))

                img = resize_image(im, w, h)
                if not is_pro:
                    img = add_watermark(img)

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
# Single size export (Pro only)
# ---------------------------------------------------------
def single_export(image_pil, orientation, group, size_choice, is_pro: bool):
    if image_pil is None:
        raise gr.Error("Upload an image first.")
    if not group:
        raise gr.Error("Choose a group.")
    if not size_choice:
        raise gr.Error("Choose a size.")

    if not is_pro:
        raise gr.Error("Demo mode: Single Export is Pro only. Unlock Pro to use this feature.")

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
with gr.Blocks(title=APP_NAME, elem_id="app-root") as app:
    is_pro = gr.State(False)

    gr.Markdown(
        f"""
# **{APP_NAME}**
Fast, clean, high-quality print preparation — without the guesswork.

### Free (Demo)
- Watermarked exports  
- Only **{', '.join(DEMO_GROUPS)}** group

### Pro
- **$12 / month** or **$99 / year (Best value)**
- No watermark  
- All print sizes  
- Advanced export  
- Cancel anytime

        """,
        elem_id="hero-text",
    )
   # ==================== UPGRADE + UNLOCK ====================
    with gr.Accordion("Unlock Pro", open=True):
        gr.Markdown("### Choose a plan")

        with gr.Row():
            if STRIPE_LINK:
                gr.Markdown(f"**Monthly — $12**  \n[{STRIPE_LINK}]({STRIPE_LINK})")

            if STRIPE_LINK_YEARLY:
                gr.Markdown(
                    f"**Yearly — $99 (Best value)**  \n[{STRIPE_LINK_YEARLY}]({STRIPE_LINK_YEARLY})"
                )

        gr.Markdown(
            """
**After purchase:** you’ll be redirected back here and Pro unlocks automatically.  
**Not redirected?** Enter your checkout email below.
            """
        )

        email_in = gr.Textbox(
            label="Email used at checkout",
            placeholder="you@example.com",
        )

        check_btn = gr.Button("Unlock Pro")
        unlock_status = gr.Markdown("")

        def unlock(email):
            ok, msg = stripe_is_pro(email)
            return ok, msg

        check_btn.click(
            fn=unlock,
            inputs=[email_in],
            outputs=[is_pro, unlock_status],
        )

    # Auto-unlock via Stripe redirect: ?session_id=cs_...
    def auto_unlock(request: gr.Request):
        session_id = None
        try:
            session_id = request.query_params.get("session_id")
        except Exception:
            session_id = None

        if not session_id:
            return False, ""

        session_id = session_id.strip()

        # Only verify real Stripe checkout sessions
        if not session_id.startswith("cs_"):
            return False, ""

        ok, msg = stripe_unlock_from_session(session_id)

        if not ok:
            return False, "Could not auto-unlock. Please use your checkout email below."

        return True, msg

    app.load(
        fn=auto_unlock,
        inputs=None,
        outputs=[is_pro, unlock_status],
        queue=False,
    )

    # Auto-unlock via Stripe redirect: ?session_id=cs_...
    def auto_unlock(request: gr.Request):
        session_id = None
        try:
            session_id = request.query_params.get("session_id")
        except Exception:
            session_id = None

        if not session_id:
            return False, ""

        session_id = session_id.strip()

        # Only verify real Stripe checkout sessions
        if not session_id.startswith("cs_"):
            return False, ""

        ok, msg = stripe_unlock_from_session(session_id)

        if not ok:
            return False, "Could not auto-unlock. Please use your checkout email below."

        return True, msg

    app.load(
        fn=auto_unlock,
        inputs=None,
        outputs=[is_pro, unlock_status],
        queue=False,
    )

    # ==================== BATCH ZIP ====================
    with gr.Tab("Batch ZIP", elem_id="tab-batch"):
        gr.Markdown(
            f"⚠️ Etsy limit: **{MAX_ZIP_SIZE_MB}MB per ZIP** and **max 5 files** per listing.",
            elem_classes=["tab-description"],
        )

        with gr.Row(elem_id="batch-row"):
            input_img = gr.Image(type="filepath", label="Upload image", elem_id="batch-input-image")
            output_zip = gr.Files(label="Download ZIPs", elem_id="batch-output-zip")

        group_select = gr.CheckboxGroup(
            GROUP_ORDER,
            label="Select print groups",
            elem_id="batch-group-select",
        )

        with gr.Row(elem_id="batch-actions-row"):
            gr.Button("Select all groups", elem_classes=["secondary"]).click(select_all_groups, [], group_select)
            gr.Button("Clear selection", elem_classes=["secondary"]).click(clear_all_groups, [], group_select)

        gr.Button("Generate ZIPs", elem_id="batch-generate-btn").click(
            generate_zip,
            inputs=[input_img, group_select, is_pro],
            outputs=output_zip,
        )

    # ==================== SINGLE EXPORT (ADVANCED) ====================
    with gr.Tab("Single Size Export (Advanced)", elem_id="tab-single-export"):
        gr.Markdown(
            "## Single Size Export (Advanced)\n"
            "_Export one specific print size from the same presets used in Batch ZIP._",
            elem_id="single-export-header",
        )

        with gr.Row(elem_id="single-row"):
            single_img = gr.Image(type="pil", label="Upload image", elem_id="single-input-image")
            single_out = gr.File(label="Download JPG", elem_id="single-output-file")

        with gr.Row(elem_id="single-controls-row"):
            orientation = gr.Radio(["Portrait", "Landscape"], value="Portrait", label="Orientation", elem_id="single-orientation")
            single_group = gr.Dropdown(GROUP_ORDER, value="4x5", label="Ratio family", elem_id="single-group")

            initial_choices, _ = build_size_map("4x5", "Portrait")
            single_size = gr.Dropdown(
                initial_choices,
                value=(initial_choices[0] if initial_choices else None),
                label="Size",
                elem_id="single-size",
            )

        orientation.change(update_single_size_choices, inputs=[orientation, single_group], outputs=single_size)
        single_group.change(update_single_size_choices, inputs=[orientation, single_group], outputs=single_size)

        gr.Button("Export JPG", elem_id="single-export-btn").click(
            single_export,
            inputs=[single_img, orientation, single_group, single_size, is_pro],
            outputs=single_out,
        )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "7860"))
    host = "0.0.0.0" if os.getenv("SPACE_ID") else "127.0.0.1"

    # Gradio 6: pass CSS to launch() (not Blocks)
    app.launch(server_name=host, server_port=port, css=CUSTOM_CSS)

