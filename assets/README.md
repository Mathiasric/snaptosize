# EtsyResizer-PRO

**EtsyResizer-PRO** is a lightning-fast batch resizer for digital art sellers, built for Etsy and other marketplaces. Drop your high-res artwork into the input folder, and get fully organized, ready-to-upload ZIP files in seconds – optimized for all standard ratios and sizes.

---

## 📦 Features

- 🔁 Auto-crops & resizes to perfect Etsy print sizes
- 💾 Instant ZIPs by ratio – 2:3, 3:4, 4:5, ISO, Extras (5x7", 8.5x11", 11x14")
- 🖼️ Maintains image quality with high-end resizing (LANCZOS filter)
- ✂️ Crops intelligently based on target aspect ratio
- 📂 Organized output with timestamped folders
- 🔐 Optional ZIP splitting for Etsy's 20 MB upload limit
- 🧾 Easily configurable – great for automation

---

## 📐 Print Sizes Supported

| Ratio | Sizes |
|-------|-------|
| **2:3** | 4×6", 8×12", 10×15", 12×18", 16×24", 20×30" |
| **3:4** | 6×8", 9×12", 12×16", 15×20", 18×24" |
| **4:5** | 4×5", 8×10", 12×15", 16×20", 20×25" |
| **11×14** | 11×14" |
| **ISO A-series** | A1, A2, A3, A4, A5 |
| **Extras** | 5×7", 8.5×11", 11×14" |

---

## 🚀 Quick Start

1. Clone or download the project  
2. Place your artwork in the `/input/` folder  
3. Run:

```bash
python src/main.py

.venv\Scripts\activate
python src\webapp.py
