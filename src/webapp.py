import json
import os
import tempfile
import zipfile
from pathlib import Path
from datetime import datetime
import requests

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

custom_css = """
/* Kill Gradio footer + API/settings bar */
footer, .built-with { display: none !important; }
.gradio-container > footer { display: none !important; }
a[href*="gradio"] { display: none !important; }
.gradio-container { padding-bottom: 0 !important; margin-bottom: 0 !important; }

/* --- FIX Gradio image preview scaling (safe) --- */
.gradio-container .gradio-image img,
.gradio-container .gradio-image canvas {
  width: auto !important;
  height: auto !important;
  max-width: 100% !important;
  max-height: 100% !important;
  object-fit: contain !important;
}
"""

# ---------------------------------------------------------
# Constants
# ---------------------------------------------------------
JPEG_QUALITY = 80
DPI = (300, 300)

MAX_ZIP_SIZE_MB = 20
MAX_ZIP_SIZE_BYTES = MAX_ZIP_SIZE_MB * 1024 * 1024

APP_NAME = "SnapToSize"
PPI = 300  # 300 DPI/PPI export for print

WORKER_BASE = "https://worker.snaptosize-mathias.workers.dev"
print("### RUNNING src/webapp.py ###", WORKER_BASE)

# ---------------------------------------------------------
# Paywall (Stripe = source of truth)
# ---------------------------------------------------------
STRIPE_LINK = os.getenv("STRIPE_LINK", "").strip()  # your monthly payment link (for UI)
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_LINK_YEARLY = os.getenv("STRIPE_LINK_YEARLY", "").strip()

if not STRIPE_SECRET_KEY:
    print("⚠️ STRIPE_SECRET_KEY not set. Running in DEV mode (Pro unlock disabled).")
    STRIPE_SECRET_KEY = "dev"

stripe.api_key = STRIPE_SECRET_KEY

DEMO_GROUPS = ["2x3"]
WATERMARK_TEXT = "SNAPTOSIZE DEMO"

# simple cache so we don't hit Stripe constantly
_PRO_CACHE = {}
_CACHE_TTL = 60  

_FREE_IP_LAST = {}
_FREE_COOLDOWN_SECONDS = 24 * 60 * 60

_FREE_CLIENT_LAST = {}  # session_hash fallback cache



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
    session_id = (session_id or "").strip()
    if not session_id:
        return False, "", ""

    try:
        session = stripe.checkout.Session.retrieve(
            session_id,
            expand=["subscription", "customer", "customer_details"],
        )

        if getattr(session, "payment_status", None) != "paid":
            return False, "Payment not completed yet.", ""

        sub = getattr(session, "subscription", None)
        if sub:
            sub_status = getattr(sub, "status", None)
            if sub_status not in ("active", "trialing"):
                return False, f"Subscription not active ({sub_status}).", ""

        email = ""
        cd = getattr(session, "customer_details", None)
        if cd and getattr(cd, "email", None):
            email = cd.email

        msg = "✅ Pro unlocked."
        return True, msg, email

    except Exception as e:
        return False, f"Could not verify checkout. ({type(e).__name__})", ""


def add_watermark(im: Image.Image, text: str = "SnapToSize") -> Image.Image:
    """
    Light watermark: single centered text.
    Much cheaper than tiled/diagonal stamping.
    """
    base = im.convert("RGBA")
    w, h = base.size

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Scale font to image size (safe + readable)
    font_size = max(24, int(min(w, h) * 0.06))
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    # Measure text
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    # Center position
    x = (w - tw) // 2
    y = (h - th) // 2

    # Subtle shadow for contrast
    shadow_alpha = 120
    text_alpha = 160
    draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, shadow_alpha))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, text_alpha))

    out = Image.alpha_composite(base, overlay).convert("RGB")
    return out


def _persist_email_script(email: str) -> str:
    email = (email or "").strip()
    if not email:
        return ""
    safe = email.replace("\\", "\\\\").replace("'", "\\'")
    return f"""
<script>
try {{
  localStorage.setItem('snaptosize_email', '{safe}');
}} catch(e) {{}}
</script>
"""


def _preload_and_autounlock_script() -> str:
    # Runs on page load in the browser
    return """
<script>
(function(){
  try {
    const params = new URLSearchParams(window.location.search);

    // If coming from Stripe redirect, let server-side auto_unlock handle it.
    if (params.get('session_id')) return;

    // 1) Always load "free export used" timestamp into hidden Gradio input
    const usedAt = localStorage.getItem('snaptosize_free_used_at') || "";
    const freeInput = document.querySelector('#free-state input, #free-state textarea');
    if (freeInput && !freeInput.value) {
      freeInput.value = usedAt;
      freeInput.dispatchEvent(new Event('input', { bubbles: true }));
    }

  // 2) If we have a stored checkout email, preload it + also fill hidden saved-email for backend auto-unlock
const saved = localStorage.getItem('snaptosize_email') || "";

// Fill visible email input (if present)
const input = document.querySelector('#checkout-email input');
if (input && !input.value && saved) {
  input.value = saved;
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

// Fill hidden email input used for app.load auto-unlock (must run even if visible field isn't there)
const emailHidden = document.querySelector('#saved-email input, #saved-email textarea');
if (emailHidden && !emailHidden.value) {
  emailHidden.value = saved;
  emailHidden.dispatchEvent(new Event('input', { bubbles: true }));
}



    // Click unlock after a short delay (Gradio needs to mount)
    setTimeout(function(){
      const btn = document.querySelector('#unlock-btn button');
      if (btn) btn.click();
    }, 600);

  } catch(e) {}
})();
</script>
"""

# ---------------------------------------------------------
# Utilities
# ---------------------------------------------------------
def normalize_image(im: Image.Image) -> Image.Image:
    """Fix EXIF rotation + ensure RGB + downscale huge images."""
    im = ImageOps.exif_transpose(im)

    if im.mode != "RGB":
        im = im.convert("RGB")

    # 🔒 HARD SIZE LIMIT (prevents huge uploads killing UI/memory)
    MAX_INPUT_PX = 10000  # safe, generous, print-quality friendly
    w, h = im.size
    if max(w, h) > MAX_INPUT_PX:
        scale = MAX_INPUT_PX / max(w, h)
        im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

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


def get_client_ip(request: gr.Request) -> str | None:
    """Best-effort client IP behind proxies (HF/Cloudflare/etc)."""
    try:
        h = request.headers or {}
    except Exception:
        h = {}

    # keys can vary in casing
    def _get(k: str):
        return h.get(k) or h.get(k.lower()) or h.get(k.upper())

    # Prefer CF / reverse proxy headers
    ip = _get("cf-connecting-ip")
    if ip:
        return ip.strip()

    xff = _get("x-forwarded-for")
    if xff:
        # can be "client, proxy1, proxy2"
        return xff.split(",")[0].strip()

    xri = _get("x-real-ip")
    if xri:
        return xri.strip()

    # Fallback
    try:
        return request.client.host
    except Exception:
        return None


def get_client_id(request: gr.Request) -> str:
    """Stable-ish identifier per browser session if available, else IP."""
    # Gradio often has session_hash internally
    sid = getattr(request, "session_hash", None)
    if sid:
        return f"sid:{sid}"

    ip = get_client_ip(request)
    if ip:
        return f"ip:{ip}"

    # last resort: something constant
    return "unknown"

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
def generate_zip(image_path, groups, is_pro: bool, free_used_at: str, request: gr.Request = None):
    print("generate_zip START", {"groups": groups, "is_pro": is_pro})
    if not image_path:
        raise gr.Error("Upload an image first.")
    if not groups:
        raise gr.Error("Choose at least one group.")

    now = time.time()

    # -----------------------------
    # FREE LIMIT (HARD)
    # -----------------------------
    if not is_pro:
        paywall_msg = (
            "Your files are ready. Upgrade to download clean Etsy-ready ZIPs (no watermark, unlimited exports)."
        )
        if STRIPE_LINK or STRIPE_LINK_YEARLY:
            paywall_msg += "\n\n"
            if STRIPE_LINK:
                paywall_msg += f"Monthly: {STRIPE_LINK}\n"
            if STRIPE_LINK_YEARLY:
                paywall_msg += f"Yearly: {STRIPE_LINK_YEARLY}"
            paywall_msg = paywall_msg.strip()

        # 1) Server-side client cooldown (best)
        client_id = get_client_id(request)
        last = _FREE_CLIENT_LAST.get(client_id, 0.0)
        if last and (now - last) < _FREE_COOLDOWN_SECONDS:
            raise gr.Error(paywall_msg)

        # 2) localStorage cooldown (cross-refresh)
        try:
            used_ts = float(free_used_at) if free_used_at else 0.0
        except Exception:
            used_ts = 0.0

        if used_ts and (now - used_ts) < _FREE_COOLDOWN_SECONDS:
            raise gr.Error(paywall_msg)

        # 3) IP cooldown (best-effort anti-incognito)
        ip = get_client_ip(request)
        if ip:
            last = _FREE_IP_LAST.get(ip, 0.0)
            if last and (now - last) < _FREE_COOLDOWN_SECONDS:
                raise gr.Error(paywall_msg)


    im = normalize_image(Image.open(image_path))
    run_dir = make_run_dir()
    result_files = []

    for group in groups:
        zip_path = run_dir / f"{group}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
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
                with zf.open(filename, "w") as f:
                    img.save(f, "JPEG", quality=JPEG_QUALITY, dpi=DPI)

        ensure_under_etsy_limit(str(zip_path))
        result_files.append(str(zip_path))

    # -----------------------------
    # MARK FREE EXPORT AS USED
    # -----------------------------
    js = ""

    if not is_pro:
        now = time.time()
        try:
            client_id = get_client_id(request)
            _FREE_CLIENT_LAST[client_id] = now
            if ip:
               _FREE_IP_LAST[ip] = now

        except Exception:
            pass

        js = f"""
<script>
try {{
  localStorage.setItem('snaptosize_free_used_at', '{now}');
  const freeInput = document.querySelector('#free-state input, #free-state textarea');
  if (freeInput) {{
    freeInput.value = '{now}';
    freeInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
  }}
}} catch(e) {{}}
</script>
"""

    print("generate_zip DONE", {"zips": len(result_files)})
    return result_files, js


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
# New Engine (Async) - Worker pipeline
# ---------------------------------------------------------
ASYNC_PRESETS = ["thumb_1024", "etsy_3000px", "etsy_6000px"]


def enqueue_job(image_url: str, presets: list) -> str:
    url = f"{WORKER_BASE}/enqueue"
    payload = {
        "image_url": (image_url or "").strip(),
        "presets": presets or ASYNC_PRESETS,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json,text/plain,*/*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36",
        "Origin": "http://localhost:7860",
        "Referer": "http://localhost:7860/",
    }
    r = requests.post(url, json=payload, headers=headers, timeout=20)
    if r.status_code != 200:
        raise gr.Error(f"ENQUEUE HTTP {r.status_code}: {r.text[:200]}")
    return r.json()["job_id"]


def poll_status(job_id: str, timeout_s: int = 90) -> dict:
    start = time.time()
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36",
    }
    while time.time() - start < timeout_s:
        url = f"{WORKER_BASE}/status/{job_id}"
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code != 200:
            raise gr.Error(f"STATUS HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        if data.get("status") == "done":
            return data
        if data.get("status") == "error":
            raise gr.Error(f"Job error: {data}")
        time.sleep(1)
    raise gr.Error("Timed out waiting for job")


def generate_async(image_url: str, presets: list) -> str:
    job_id = enqueue_job(image_url, presets)
    data = poll_status(job_id)
    result = data.get("result", data)
    presets_list = result.get("presets", [])
    lines = [f"**job_id:** `{job_id}`", f"**status:** done", ""]
    download_url = data.get("download_url")
    if download_url:
        lines.append(f"**[Download ZIP]({download_url})**")
        lines.append("")
    if presets_list:
        lines.append("| name | width × height | jpeg_bytes | MB |")
        lines.append("|------|----------------|------------|-----|")
        for p in presets_list:
            w = p.get("width", 0)
            h = p.get("height", 0)
            jb = p.get("jpeg_bytes", 0)
            mb = f"{jb / 1024 / 1024:.2f}"
            lines.append(f"| {p.get('name', '')} | {w} × {h} | {jb} | {mb} |")
    else:
        lines.append("_No presets in result._")
    return "\n".join(lines)


# ---------------------------------------------------------
# UI
# ---------------------------------------------------------
def render_pro_badge(ok: bool) -> str:
    return "🟣 **Pro active** — unlimited exports enabled." if ok else ""

with gr.Blocks(title=APP_NAME, elem_id="app-root") as app:
    pro_badge = gr.Markdown("", elem_id="pro-badge")
    is_pro = gr.State(False)

    gr.HTML(
        f"""
<div class="hero">
  <h1 class="hero-title">{APP_NAME}</h1>
  <p class="hero-sub">Fast, clean, high-quality print preparation — without the guesswork.</p>

  <div class="plan-grid">
    <div class="plan-card">
      <div class="plan-head">
        <div class="plan-name">Free (Demo)</div>
        <div class="plan-price">1 export</div>
      </div>
      <ul class="plan-list">
        <li>1 image</li>
        <li>All print sizes + ZIP</li>
        <li>Watermark included</li>
        <li>Best for previewing quality</li>
      </ul>
    </div>

    <div class="plan-card plan-pro">
      <div class="plan-head">
        <div class="plan-name">Pro</div>
        <div class="plan-price">$11.99 / mo · $97 / yr</div>
      </div>
      <ul class="plan-list">
        <li>No watermark</li>
        <li>Unlimited exports</li>
        <li>All print sizes</li>
        <li>Advanced export</li>
        <li>Cancel anytime</li>
      </ul>
    </div>
  </div>
</div>
""",
        elem_id="hero-text",
    )

    # ==================== UPGRADE + UNLOCK ====================
    with gr.Accordion("Unlock Pro", open=False):
        gr.Markdown("### Choose a plan")

        with gr.Row():
            if STRIPE_LINK:
                gr.Markdown(f"**Monthly — $11.99**  \n[{STRIPE_LINK}]({STRIPE_LINK})")

            if STRIPE_LINK_YEARLY:
                gr.Markdown(
                    f"**Yearly — $97 (Best value  · Save ~33%)**  \n[{STRIPE_LINK_YEARLY}]({STRIPE_LINK_YEARLY})"
                )

        gr.Markdown(
            """
✅ **Pro unlocks automatically after checkout.**

Didn’t unlock automatically?  
Paste the email you used at checkout and click **Unlock Pro**.
            """
        )

        email_in = gr.Textbox(
            label="Checkout email (only if needed)",
            placeholder="you@example.com",
            elem_id="checkout-email",
        )

        check_btn = gr.Button("Unlock Pro", elem_id="unlock-btn")
        unlock_status = gr.Markdown("")

        preload_js = gr.HTML(_preload_and_autounlock_script(), elem_id="preload-js")
        persist_js = gr.HTML("", elem_id="persist-js")

        free_state = gr.Textbox(value="", visible=False, elem_id="free-state")
        saved_email = gr.Textbox(value="", visible=False, elem_id="saved-email")
        free_js = gr.HTML("", elem_id="free-js")

        def unlock(email):
            ok, msg = stripe_is_pro(email)
            js = _persist_email_script(email) if ok else ""
            badge = render_pro_badge(ok)
            return ok, msg, js, badge

        check_btn.click(
            fn=unlock,
            inputs=[email_in],
            outputs=[is_pro, unlock_status, persist_js, pro_badge],
        )

    # Auto-unlock via Stripe redirect (?session_id=cs_...) OR saved email
    def auto_unlock(saved_email_value: str, request: gr.Request):
        # 1) Stripe redirect unlock (?session_id=cs_...)
        session_id = None
        try:
            session_id = request.query_params.get("session_id")
        except Exception:
            session_id = None

        if session_id:
            session_id = session_id.strip()
            if session_id.startswith("cs_"):
                ok, msg, email = stripe_unlock_from_session(session_id)
                js = _persist_email_script(email) if ok else ""
                badge = render_pro_badge(ok)
                return ok, (msg if ok else ""), js, badge

        # 2) Auto-unlock from saved email
        email = (saved_email_value or "").strip()
        if not email:
            return False, "", "", ""

        ok, msg = stripe_is_pro(email)
        badge = render_pro_badge(ok)
        return ok, (msg if ok else ""), "", badge

    app.load(
        fn=auto_unlock,
        inputs=[saved_email],
        outputs=[is_pro, unlock_status, persist_js, pro_badge],
        queue=False,
    )

    # ==================== BATCH ZIP ====================
    with gr.Tab("Batch ZIP", elem_id="tab-batch"):
        gr.Markdown(
            f"⚠️ Etsy limit: **{MAX_ZIP_SIZE_MB}MB per ZIP** and **max 5 files** per listing.",
            elem_classes=["tab-description"],
        )

        with gr.Row(elem_id="batch-row"):
            input_img = gr.Image(
                type="filepath",
                label="Upload image (JPG recommended)",
                height=320,
                elem_id="batch-input-image",
            )
            output_zip = gr.Files(label="Download ZIPs", elem_id="batch-output-zip")

        group_select = gr.CheckboxGroup(
            GROUP_ORDER,
            label="Select print groups",
            elem_id="batch-group-select",
        )

        with gr.Row(elem_id="batch-actions-row"):
            gr.Button("Select all groups", elem_classes=["secondary"]).click(
                select_all_groups,
                inputs=[],
                outputs=group_select,
                queue=False,
            )
            gr.Button("Clear selection", elem_classes=["secondary"]).click(
                clear_all_groups,
                inputs=[],
                outputs=group_select,
                queue=False,
            )

        gr.Button("Generate ZIPs", elem_id="batch-generate-btn").click(
            fn=generate_zip,
            inputs=[input_img, group_select, is_pro, free_state],
            outputs=[output_zip, free_js],
            queue=False,
        )

    # ==================== NEW ENGINE (ASYNC) ====================
    with gr.Tab("New Engine (Async)", elem_id="tab-async"):
        gr.Markdown(
            "_Async pipeline: image_url → Worker → Runner → preset metadata._",
            elem_classes=["tab-description"],
        )
        async_image_url = gr.Textbox(
            label="Image URL",
            placeholder="https://example.com/image.jpg",
            elem_id="async-image-url",
        )
        async_presets = gr.CheckboxGroup(
            ASYNC_PRESETS,
            value=ASYNC_PRESETS,
            label="Presets",
            elem_id="async-presets",
        )
        async_btn = gr.Button("Generate (Async)", elem_id="async-generate-btn")
        async_out = gr.Markdown("", elem_id="async-output")
        async_btn.click(
            fn=generate_async,
            inputs=[async_image_url, async_presets],
            outputs=async_out,
        )

    # ==================== SINGLE EXPORT (ADVANCED) ====================
    with gr.Tab("Single Size Export (Advanced)", elem_id="tab-single-export"):
        gr.Markdown(
            "## Single Size Export (Advanced)\n"
            "_Export one specific print size from the same presets used in Batch ZIP._",
            elem_id="single-export-header",
        )

        with gr.Row(elem_id="single-row"):
            single_img = gr.Image(type="pil", label="Upload image (JPG recommended)", height=320, elem_id="single-input-image")
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


