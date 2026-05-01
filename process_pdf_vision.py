"""
Extract text from image-only PDF using Claude vision, then generate summary + source files.
Usage: python3 process_pdf_vision.py <pdf_path> <pdf_number>
Example: python3 process_pdf_vision.py "inbox/2023_09_19 14_06 Office Lens DO NOT CHANGE THIS BOOK FILE EVER.pdf" 3
"""
import sys
import os
import re
import base64
import json
from pathlib import Path
import fitz  # PyMuPDF
import anthropic

WIKI_ROOT = Path(__file__).parent
EPISODES_DIR = WIKI_ROOT / "docs" / "Episodes"
MAP_FILE = WIKI_ROOT / "docs" / "MAP.md"
CONCLUSIONS_DIR = WIKI_ROOT / "docs" / "Conclusions"

def load_api_key():
    env = WIKI_ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip()
    parent_env = WIKI_ROOT.parent.parent.parent.parent.parent / ".env"
    if parent_env.exists():
        for line in parent_env.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("ANTHROPIC_API_KEY", "")

def pdf_pages_to_images(pdf_path: Path, dpi: int = 150):
    """Render each PDF page to a PNG image, return list of base64-encoded PNGs."""
    doc = fitz.open(str(pdf_path))
    images = []
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    for page in doc:
        pix = page.get_pixmap(matrix=mat)
        png_bytes = pix.tobytes("png")
        images.append(base64.standard_b64encode(png_bytes).decode())
    doc.close()
    print(f"  Rendered {len(images)} pages at {dpi} DPI")
    return images

def extract_text_from_images(images: list, client: anthropic.Anthropic) -> str:
    """Send all page images to Claude vision in batches, extract all text."""
    all_text = []
    batch_size = 5  # pages per request to avoid token limits

    for i in range(0, len(images), batch_size):
        batch = images[i:i + batch_size]
        start_page = i + 1
        end_page = i + len(batch)
        print(f"  Extracting text from pages {start_page}–{end_page}...")

        content = []
        for j, img_b64 in enumerate(batch):
            content.append({
                "type": "text",
                "text": f"Page {start_page + j}:"
            })
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": img_b64
                }
            })

        content.append({
            "type": "text",
            "text": (
                "Extract ALL text from these scanned pages exactly as written. "
                "Preserve headings, bullet points, paragraphs, and formatting. "
                "If a page is blank or illegible, write '[blank page]'. "
                "Output only the extracted text, no commentary."
            )
        })

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            messages=[{"role": "user", "content": content}]
        )
        all_text.append(response.content[0].text)

    return "\n\n---\n\n".join(all_text)

def generate_summary(full_text: str, pdf_filename: str, pdf_number: int, client: anthropic.Anthropic) -> str:
    """Generate structured summary.md content from extracted text."""
    print("  Generating summary via Claude Haiku...")
    capped = full_text[:60000]

    prompt = f"""You are summarizing a scanned PDF document for an immortality/longevity research wiki.

PDF filename: {pdf_filename}
PDF number: PDF-{pdf_number}

Full extracted text:
{capped}

Write a structured summary.md with this EXACT format (no markdown code fences):

---
title: "Summary: [Descriptive Title Based on Content]"
---

# [Descriptive Title Based on Content]

> 📄 [View Full Source](source.md)

## Overview
[2-3 paragraph synthesis of what this document is about and its key themes]

## Key Insights
* **[Insight title]:** [2-3 sentence explanation]
[5-10 bullet points]

## Core Concepts
* **[Concept]:** [Definition/explanation]
[3-8 bullet points]

## Practical Takeaways
* [Actionable item]
[3-8 bullet points]

## Topics
[comma-separated slugs from this list that apply: diet-and-nutrition, exercise-and-fitness, sleep-and-recovery, supplements-and-protocols, biomarkers-and-testing, mental-health, longevity-science, skin-and-anti-aging, environment-and-toxins, hormones-and-sexual-health, technology-and-ai, gut-health, mindset-and-philosophy]

Do not include ## Guest section. Do not add ## See Also."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )
    text = response.content[0].text.strip()
    # Strip any accidental code fences
    if text.startswith("```"):
        text = re.sub(r'^```\w*\n', '', text)
        text = re.sub(r'\n```$', '', text)
    return text

def safe_title(filename: str) -> str:
    """Convert PDF filename to safe folder title."""
    name = Path(filename).stem
    name = re.sub(r'[<>:"/\\|?*#%()\'…]', '', name)
    name = name.replace('&', 'and').replace('|', '-')
    name = re.sub(r'\s+', ' ', name).strip()
    return name

def parse_title_from_summary(summary_text: str) -> str:
    """Extract the H1 title from generated summary."""
    match = re.search(r'^# (.+)$', summary_text, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return "Unknown PDF Document"

def parse_topics_from_summary(summary_text: str) -> list:
    """Extract topic slugs from ## Topics section."""
    match = re.search(r'^## Topics\s*\n(.+?)(?=\n##|\Z)', summary_text, re.MULTILINE | re.DOTALL)
    if match:
        raw = match.group(1).strip()
        return [t.strip() for t in raw.split(',') if t.strip()]
    return []

def update_conclusions(topics: list, title: str, folder_name: str):
    """Append entry to each relevant Conclusions file."""
    rel_path = f"../Episodes/{folder_name}/summary.md"
    entry = f"\n### [{title}]({rel_path})\n\nExtracted from scanned PDF document. Contains research and insights relevant to this topic area.\n\n"

    for slug in topics:
        cf = CONCLUSIONS_DIR / f"{slug}.md"
        if cf.exists():
            current = cf.read_text()
            cf.write_text(current + entry)
            print(f"  Updated Conclusions: {slug}.md")

def update_map(title: str, folder_name: str, pdf_number: int):
    """Append PDF entry to MAP.md."""
    entry = f"- [PDF-{pdf_number} — {title}](Episodes/{folder_name}/summary.md)\n"
    current = MAP_FILE.read_text()
    MAP_FILE.write_text(current + entry)
    print(f"  Updated MAP.md")

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 process_pdf_vision.py <pdf_path> <pdf_number>")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    pdf_number = int(sys.argv[2])

    if not pdf_path.exists():
        pdf_path = WIKI_ROOT / sys.argv[1]
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}")
        sys.exit(1)

    api_key = load_api_key()
    if not api_key:
        print("No ANTHROPIC_API_KEY found")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    print(f"\n=== Processing: {pdf_path.name} ===")

    # Step 1: Render pages to images
    print("\n[1/5] Rendering PDF pages to images...")
    images = pdf_pages_to_images(pdf_path)

    # Step 2: Extract text via Claude vision
    print("\n[2/5] Extracting text via Claude vision...")
    full_text = extract_text_from_images(images, client)
    print(f"  Extracted {len(full_text)} characters")

    # Step 3: Generate summary
    print("\n[3/5] Generating summary...")
    summary_text = generate_summary(full_text, pdf_path.name, pdf_number, client)
    title = parse_title_from_summary(summary_text)
    topics = parse_topics_from_summary(summary_text)
    print(f"  Title: {title}")
    print(f"  Topics: {topics}")

    # Step 4: Write files
    print("\n[4/5] Writing episode files...")
    safe = safe_title(pdf_path.name)
    folder_name = f"PDF-{pdf_number} - {safe}"
    folder = EPISODES_DIR / folder_name
    folder.mkdir(parents=True, exist_ok=True)

    # summary.md
    (folder / "summary.md").write_text(summary_text + "\n")

    # source.md
    source_content = f"""---
title: "Source: {title}"
search:
  exclude: true
---

# Source: {title}

**Original file:** `{pdf_path.name}`

---

{full_text}
"""
    (folder / "source.md").write_text(source_content)
    print(f"  Written: {folder_name}/summary.md + source.md")

    # Step 5: Update MAP.md and Conclusions
    print("\n[5/5] Updating MAP.md and Conclusions...")
    update_map(title, folder_name, pdf_number)
    if topics:
        update_conclusions(topics, title, folder_name)

    print(f"\n✓ Done — PDF-{pdf_number} processed as: {folder_name}")
    print(f"  Build check: python3 -m mkdocs build 2>&1 | grep 'WARNING -  Doc file'")

if __name__ == "__main__":
    main()
