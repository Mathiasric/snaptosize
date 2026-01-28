---
title: SnapToSize
sdk: gradio
python_version: 3.11
---

# SnapToSize

**SnapToSize** is a smart image resizing web app for creators who sell digital prints, photos, and artwork.

Upload one image → get **all professional print sizes** exported automatically.  
No cropping. No guessing. No manual work.

Built for:
- Etsy sellers
- Print shops
- Photographers
- Content creators

---

## 🚀 What SnapToSize Does

- Upload **one image**
- Automatically resizes it to **all common print ratios**
- Preserves full image content (no cropping)
- Exports everything as **ready-to-sell ZIP files**
- Runs as a **web app** (Gradio + Python)

This is designed to become a **paid SaaS**, not just a local script.

---

## 📦 Features

- 🧠 Smart resize (no cropping, no stretching)
- 🖼️ High-quality scaling (LANCZOS)
- 📐 All major print ratios & sizes
- 📦 Auto-generated ZIPs per ratio
- ⚡ Fast, simple, single-image workflow
- 🌐 Web-based UI (no setup needed)
- 🔒 Pro mode ready (paywall planned)

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
- 4×5"
- 8×10"
- 12×15"
- 16×20"
- 20×25"

### ISO (A-Series)
- A1
- A2
- A3
- A4
- A5

### Extras
- 5×7"
- 8.5×11"
- 11×14"

---

## 🧑‍💻 Tech Stack

- Python 3.11
- Gradio
- Pillow (+ pillow-heif)
- Hugging Face Spaces

---

## 🧪 Local Development

```bash
python app.py
