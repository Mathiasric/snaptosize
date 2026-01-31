---
title: SnapToSize — Etsy-ready print files
sdk: gradio
python_version: 3.11
---

# SnapToSize

**SnapToSize** is a lightweight web app that turns a single image into **all Etsy-ready print sizes** — clean, high-quality, and ready to sell.

Upload one image → get **perfect print files + ZIPs**.  
No cropping. No accounts. No guesswork.

Built for creators who sell **digital prints**.

---

## 🎯 Who SnapToSize Is For

- Etsy sellers (digital downloads)
- Print-on-demand creators
- Photographers selling wall art
- Designers preparing print files

If you sell digital art and hate resizing the same image 10+ times — this tool is for you.

---

## 🚀 What the App Does

1. Upload **one image**
2. Select print size groups
3. SnapToSize generates:
   - All standard print sizes
   - 300 DPI files
   - Clean JPGs
   - Organized ZIP files per ratio
4. Files are **ready for Etsy upload** (20MB limit enforced)

No cropping.  
No stretching.  
No manual resizing.

---

## 🆓 Free vs Pro

### Free (Demo)
- One export
- All print sizes
- Watermarked output
- Designed to preview quality (not sellable)

### Pro
- Unlimited exports
- No watermark
- All print sizes
- Advanced single-size export
- Cancel anytime (managed via Stripe)

No accounts.  
No login.  
Stripe is the source of truth.

---

## 📦 Features

- 🖼️ High-quality image resizing (LANCZOS)
- 📐 All common print ratios and sizes
- 📦 Auto-generated ZIP files
- ⚠️ Etsy 20MB ZIP limit enforced
- 🧪 Free demo with hard limit
- 🔒 Pro unlock via Stripe Checkout
- 🌐 Stateless web app (no user accounts)

---

## 📐 Supported Print Sizes

### 2:3 Ratio
- 4×6"
- 8×12"
- 10×15"
- 12×18"
- 16×24"
- 20×30"

### 3:4 Ratio
- 6×8"
- 9×12"
- 12×16"
- 15×20"
- 18×24"

### 4:5 Ratio
- 8×10"
- 12×15"
- 16×20"
- 20×25"

### ISO (A-Series)
- A5
- A4
- A3
- A2
- A1

### Extras
- 5×7"
- 8.5×11"
- 11×14"
- 16×20"
- 20×24"

---

## 🧠 How It Works (Under the Hood)

- Python + Pillow for image processing
- High-quality resizing (no cropping)
- Each size exported at **300 DPI**
- Files grouped into ZIPs by ratio
- ZIP size validated to meet Etsy limits
- Stateless execution (safe for web hosting)

The app does **not** store images or user data.

---

## 🧑‍💻 Tech Stack

- Python 3.11
- Gradio
- Pillow
- Stripe (payments)
- Hugging Face Spaces

---

## 🧪 Local Development

```bash
pip install -r requirements.txt
python app.py

