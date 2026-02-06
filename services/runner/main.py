import os
import json
import hashlib
import zipfile

import boto3
from botocore.config import Config
from fastapi import FastAPI, Header, HTTPException, Request
import httpx
from PIL import Image
from io import BytesIO

JPEG_QUALITY = 80
DPI = (300, 300)

PRESET_LONG_SIDE = {
    "thumb_1024": 1024,
    "etsy_3000px": 3000,
    "etsy_6000px": 6000,
}


def _fit_long_side(w: int, h: int, long_side: int) -> tuple[int, int]:
    if w >= h:
        new_w = long_side
        new_h = max(1, round(h * (long_side / w)))
    else:
        new_h = long_side
        new_w = max(1, round(w * (long_side / h)))
    return int(new_w), int(new_h)


def _jpeg_size_bytes(img: Image.Image) -> int:
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return len(buf.getvalue())


def build_presets(im: Image.Image, presets: list[str] | None):
    names = presets or ["thumb_1024", "etsy_3000px", "etsy_6000px"]
    out = []
    for name in names:
        if name not in PRESET_LONG_SIDE:
            continue
        long_side = PRESET_LONG_SIDE[name]
        w, h = im.size
        nw, nh = _fit_long_side(w, h, long_side)
        resized = im.resize((nw, nh), Image.LANCZOS)
        out.append({
            "name": name,
            "width": nw,
            "height": nh,
            "jpeg_bytes": _jpeg_size_bytes(resized),
        })
    return out


def upload_zip_to_r2(zip_path: str, key: str) -> dict:
    account_id = os.environ["R2_ACCOUNT_ID"]
    access_key = os.environ["R2_ACCESS_KEY_ID"]
    secret_key = os.environ["R2_SECRET_ACCESS_KEY"]
    bucket = os.environ["R2_BUCKET"]

    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )

    extra = {"ContentType": "application/zip"}
    s3.upload_file(zip_path, bucket, key, ExtraArgs=extra)
    return {"bucket": bucket, "key": key}


app = FastAPI()
RUNNER_TOKEN = os.getenv("RUNNER_TOKEN", "").strip()

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/generate")
async def generate(request: Request, authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing/invalid Authorization header")

    token = authorization[7:].strip()
    if not RUNNER_TOKEN or token != RUNNER_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")

    job = await request.json()
    raw = json.dumps(job, separators=(",", ":"), sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()

    payload = job.get("payload") or {}
    image_url = payload.get("image_url")

    out = {
        "ok": True,
        "job_id": job.get("job_id"),
        "payload_keys": list(payload.keys()),
        "bytes": len(raw),
        "sha256": digest,
    }

    if not image_url:
        out["note"] = "No image_url provided yet"
        return out

    # Download image (hard limits)
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        headers = {
            "User-Agent": "SnapToSizeRunner/1.0 (+https://snaptosize.com)",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        r = await client.get(image_url, headers=headers)
        if r.status_code == 403:
            raise HTTPException(status_code=400, detail="image_url blocked by host (403). Use another URL or upload.")
        r.raise_for_status()
        content = r.content

    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image too large (max 25MB)")

    img = Image.open(BytesIO(content))
    img.load()

    if img.width > 15000 or img.height > 15000:
        raise HTTPException(status_code=413, detail="Image dimensions too large (max 15000px)")

    out["image"] = {
        "format": img.format,
        "mode": img.mode,
        "width": img.width,
        "height": img.height,
        "download_bytes": len(content),
    }

    # Minimal "compute": create a small thumbnail in-memory and report size (no return of bytes)
    thumb = img.copy()
    thumb.thumbnail((512, 512))
    buf = BytesIO()
    thumb.save(buf, format="JPEG", quality=82, optimize=True)
    out["thumbnail"] = {
        "width": thumb.width,
        "height": thumb.height,
        "jpeg_bytes": buf.tell(),
    }

    presets = payload.get("presets")
    preset_meta = build_presets(img, presets)
    out["presets"] = preset_meta

    # Write JPGs to disk and ZIP them
    job_id = job.get("job_id") or "unknown"
    work_dir = f"/tmp/{job_id}"
    os.makedirs(work_dir, exist_ok=True)

    out_jpg_paths = []
    preset_names = [p["name"] for p in preset_meta]
    for name in preset_names:
        if name not in PRESET_LONG_SIDE:
            continue
        long_side = PRESET_LONG_SIDE[name]
        w, h = img.size
        nw, nh = _fit_long_side(w, h, long_side)
        resized = img.resize((nw, nh), Image.LANCZOS)
        jpg_path = os.path.join(work_dir, f"{name}.jpg")
        resized.save(jpg_path, format="JPEG", quality=JPEG_QUALITY, dpi=DPI, optimize=True)
        out_jpg_paths.append(jpg_path)

    zip_path = os.path.join(work_dir, "etsy_pack_v1.zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in out_jpg_paths:
            z.write(p, arcname=os.path.basename(p))

    zip_bytes = os.path.getsize(zip_path)
    out["zip_path"] = zip_path
    out["zip_bytes"] = zip_bytes

    r2_key = f"jobs/{job_id}/etsy_pack_v1.zip"
    upload_zip_to_r2(zip_path, r2_key)
    print(f"uploaded to R2 key={r2_key}")
    out["r2_key"] = r2_key

    return out
