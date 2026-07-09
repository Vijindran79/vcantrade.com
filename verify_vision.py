"""Verify vision-model chart-reading ability (OCR of price + RSI)."""
import base64
import io
import sys
from PIL import Image, ImageDraw

OLLAMA = "http://127.0.0.1:11434"


def make_chart():
    img = Image.new("RGB", (640, 480), (255, 255, 255))
    d = ImageDraw.Draw(img)
    # simple candles
    xs = list(range(60, 600, 40))
    prices = [100, 105, 102, 110, 108, 115, 112, 120, 118, 122, 121, 125, 123, 119]
    for i, x in enumerate(xs[: len(prices)]):
        p = prices[i]
        y = 460 - int((p - 95) * 8)
        d.rectangle([x, y, x + 18, 460], fill=(30, 120, 200))
    # overlay the "labels" a real chart would show
    d.text((20, 20), "SYMBOL: MNQ1!", fill=(0, 0, 0))
    d.text((20, 45), "PRICE: 21,350.25", fill=(0, 0, 0))
    d.text((20, 70), "RSI(14): 72", fill=(200, 0, 0))
    d.text((20, 95), "MACD: +1.4", fill=(0, 120, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


def ask(model, b64, prompt):
    import requests
    try:
        r = requests.post(
            f"{OLLAMA}/api/generate",
            json={"model": model, "prompt": prompt, "images": [b64], "stream": False},
            timeout=60,
        )
        if r.status_code != 200:
            return f"[HTTP {r.status_code}]"
        return r.json().get("response", "").strip()
    except Exception as e:
        return f"[ERR {type(e).__name__}: {e}]"


def main():
    b64 = make_chart()
    prompt = (
        "This is a trading chart screenshot. Read it carefully and report ONLY "
        "these values: (1) the price, (2) the RSI(14) value, (3) the MACD value. "
        "Reply as: PRICE=<n> RSI=<n> MACD=<n>"
    )
    for m in ["moondream:latest", "qwen3-vl:2b"]:
        print(f"\n=== {m} ===")
        print(ask(m, b64, prompt))


if __name__ == "__main__":
    main()
