#!/usr/bin/env python3
"""
Rotaract Club PWA Setup Script
================================
Run once to:
  1. Generate a VAPID key pair for Web Push notifications
  2. Generate PNG app icons (72, 96, 128, 192, 512 px) from the SVG

Usage:
    python setup_pwa.py

Then copy the printed VAPID keys into your .env file.
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICONS_DIR = os.path.join(BASE_DIR, 'static', 'icons')
ENV_FILE  = os.path.join(BASE_DIR, '.env')


# ── 1. VAPID Key Generation ──────────────────────────────────────────────────

def generate_vapid_keys():
    try:
        from py_vapid import Vapid
    except ImportError:
        # pywebpush ships vapid helpers — try the alternative import
        try:
            from pywebpush import Vapid
        except ImportError:
            print("ERROR: pywebpush not installed. Run: pip install pywebpush")
            return None, None

    vapid = Vapid()
    vapid.generate_keys()

    private_key = vapid.private_key_pem.decode('utf-8').strip()
    public_key  = vapid.public_key_pem.decode('utf-8').strip()

    # Convert public key to URL-safe base64 (for the browser ApplicationServerKey)
    import base64
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat
    )
    raw_pub = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    b64_pub = base64.urlsafe_b64encode(raw_pub).rstrip(b'=').decode('utf-8')

    return private_key, b64_pub


def write_vapid_to_env(private_pem, public_b64):
    """Append VAPID keys to .env if not already present."""
    existing = ''
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            existing = f.read()

    lines = []
    if 'VAPID_PRIVATE_KEY' not in existing:
        # Store only the raw base64 PEM body (strip headers/newlines)
        import base64, re
        # Extract raw base64 from PEM body
        raw = re.sub(r'-----.*?-----', '', private_pem).replace('\n', '').strip()
        lines.append(f'VAPID_PRIVATE_KEY={raw}')
    if 'VAPID_PUBLIC_KEY' not in existing:
        lines.append(f'VAPID_PUBLIC_KEY={public_b64}')

    if lines:
        with open(ENV_FILE, 'a') as f:
            f.write('\n'.join([''] + lines + ['']))
        print(f'  ✔ VAPID keys written to {ENV_FILE}')
    else:
        print('  ℹ  VAPID keys already in .env — skipped.')


# ── 2. PNG Icon Generation ────────────────────────────────────────────────────

ICON_SIZES = [72, 96, 128, 192, 512]

def generate_png_icons():
    """Rasterise the SVG icon to PNG at multiple sizes using Pillow + cairosvg."""
    svg_path = os.path.join(ICONS_DIR, 'icon.svg')
    if not os.path.exists(svg_path):
        print(f'  ✗ SVG not found at {svg_path} — skipping PNG generation.')
        return

    os.makedirs(ICONS_DIR, exist_ok=True)

    # Try cairosvg first (best quality SVG rasteriser)
    try:
        import cairosvg
        for size in ICON_SIZES:
            out = os.path.join(ICONS_DIR, f'icon-{size}.png')
            cairosvg.svg2png(url=svg_path, write_to=out, output_width=size, output_height=size)
            print(f'  ✔ {size}×{size} icon saved (cairosvg)')
        return
    except ImportError:
        pass

    # Fallback: Pillow with a programmatically drawn icon (no cairosvg needed)
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print('  ✗ Pillow not installed. Run: pip install Pillow')
        return

    for size in ICON_SIZES:
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Blue circle background
        margin = size // 16
        draw.ellipse([margin, margin, size - margin, size - margin], fill='#0072CE')

        # Simple "RA" text in the centre
        text = 'RA'
        try:
            # Use a basic font — size is proportional
            font_size = max(size // 3, 8)
            font = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', font_size)
        except Exception:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((size - tw) // 2, (size - th) // 2 - size // 20),
                  text, fill='white', font=font)

        out = os.path.join(ICONS_DIR, f'icon-{size}.png')
        img.save(out, 'PNG')
        print(f'  ✔ {size}×{size} icon saved (Pillow fallback)')


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print('\n=== Rotaract Club PWA Setup ===\n')

    print('Step 1: Generating VAPID key pair...')
    private_pem, public_b64 = generate_vapid_keys()
    if private_pem:
        print(f'\n  Public key  (VAPID_PUBLIC_KEY):')
        print(f'  {public_b64}\n')
        write_vapid_to_env(private_pem, public_b64)
    else:
        print('  Skipped VAPID generation.')

    print('\nStep 2: Generating PNG icons...')
    generate_png_icons()

    print('\n✅ PWA setup complete!')
    print('\nNext steps:')
    print('  1. Verify .env has VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY')
    print('  2. Restart the app:  python app.py')
    print('  3. Open in browser, accept the notification prompt')
    print('  4. To install as app: tap "Add to Home Screen" on mobile\n')


if __name__ == '__main__':
    main()
