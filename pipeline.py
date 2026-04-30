#!/usr/bin/env python3
"""
Immortality Wiki Pipeline
Processes Bryan Johnson YouTube videos and PDFs into MkDocs-compatible wiki pages.
Three-tier transcript: youtube-transcript-api → yt-dlp → Playwright browser.

Usage:
  python3 pipeline.py                        # process next pending video
  python3 pipeline.py --limit 10             # process up to 10 videos
  python3 pipeline.py --video VIDEO_ID       # process specific video
  python3 pipeline.py --pdf inbox/file.pdf   # process a PDF from inbox
  python3 pipeline.py --process-inbox        # process all PDFs in inbox/
  python3 pipeline.py --fetch-channel        # refresh video list from YouTube
  python3 pipeline.py --dry-run              # preview only
"""

import json
import os
import re
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent
PROGRESS_FILE = BASE / "PROGRESS.json"
DOCS_DIR = BASE / "docs"
CONCLUSIONS_DIR = DOCS_DIR / "Conclusions"
MAP_FILE = DOCS_DIR / "MAP.md"
GUESTS_FILE = DOCS_DIR / "GUESTS.md"
EPISODES_DIR = DOCS_DIR / "Episodes"
INBOX_DIR = BASE / "inbox"
EPISODES_DIR.mkdir(exist_ok=True)
INBOX_DIR.mkdir(exist_ok=True)

CHANNEL_URL = "https://www.youtube.com/@BryanJohnson/videos"
COOKIES_FILE = BASE / "cookies.txt"

def _load_api_key():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    for env_path in [BASE / ".env", BASE.parent.parent.parent.parent / ".env"]:
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"')
    return None

ANTHROPIC_API_KEY = _load_api_key()

# ── Topic map ─────────────────────────────────────────────────────────────────
TOPIC_FILES = {
    "diet-and-nutrition": CONCLUSIONS_DIR / "diet-and-nutrition.md",
    "exercise-and-fitness": CONCLUSIONS_DIR / "exercise-and-fitness.md",
    "sleep-and-recovery": CONCLUSIONS_DIR / "sleep-and-recovery.md",
    "supplements-and-protocols": CONCLUSIONS_DIR / "supplements-and-protocols.md",
    "biomarkers-and-testing": CONCLUSIONS_DIR / "biomarkers-and-testing.md",
    "mental-health": CONCLUSIONS_DIR / "mental-health.md",
    "longevity-science": CONCLUSIONS_DIR / "longevity-science.md",
    "skin-and-anti-aging": CONCLUSIONS_DIR / "skin-and-anti-aging.md",
    "environment-and-toxins": CONCLUSIONS_DIR / "environment-and-toxins.md",
    "hormones-and-sexual-health": CONCLUSIONS_DIR / "hormones-and-sexual-health.md",
    "technology-and-ai": CONCLUSIONS_DIR / "technology-and-ai.md",
    "gut-health": CONCLUSIONS_DIR / "gut-health.md",
    "mindset-and-philosophy": CONCLUSIONS_DIR / "mindset-and-philosophy.md",
}

TOPIC_LABELS = {
    "diet-and-nutrition": "Diet & Nutrition",
    "exercise-and-fitness": "Exercise & Fitness",
    "sleep-and-recovery": "Sleep & Recovery",
    "supplements-and-protocols": "Supplements & Protocols",
    "biomarkers-and-testing": "Biomarkers & Testing",
    "mental-health": "Mental Health",
    "longevity-science": "Longevity Science",
    "skin-and-anti-aging": "Skin & Anti-Aging",
    "environment-and-toxins": "Environment & Toxins",
    "hormones-and-sexual-health": "Hormones & Sexual Health",
    "technology-and-ai": "Technology & AI",
    "gut-health": "Gut Health",
    "mindset-and-philosophy": "Mindset & Philosophy",
}

VALID_TOPICS = set(TOPIC_FILES.keys())

# ── Summary prompts ───────────────────────────────────────────────────────────
VIDEO_SUMMARY_PROMPT = """\
You are building the Immortality Wiki — a knowledge base on longevity, anti-aging, and human health optimization based on Bryan Johnson's research and content.

Given the transcript of a Bryan Johnson YouTube video, produce a structured markdown summary.

Video title: {title}
Video ID: {video_id}
YouTube URL: https://www.youtube.com/watch?v={video_id}

Transcript:
{transcript}

---

Output ONLY valid markdown (no code fences, no preamble) with this exact structure:

# {title}

> 📄 [View Full Transcript](transcript.md)

**YouTube:** [Watch on YouTube](https://www.youtube.com/watch?v={video_id})

## Overview

[2-3 sentence overview of what this video covers and its core thesis]

## Key Insights

[5-10 bullet points with the most important insights, data points, or findings. Be specific and include numbers where mentioned.]

## Core Concepts

[Key concepts, protocols, or frameworks discussed with 1-2 sentence explanations each]

## Practical Takeaways

[3-7 concrete, actionable things the viewer can do based on this video]

## Key Learnings & Conclusions

[2-4 sentence synthesis — the most important conclusion from this content that belongs in a longevity knowledge base]

## Topics

topics: [comma-separated list of 1-3 most relevant topic slugs from:
diet-and-nutrition, exercise-and-fitness, sleep-and-recovery, supplements-and-protocols, biomarkers-and-testing, mental-health, longevity-science, skin-and-anti-aging, environment-and-toxins, hormones-and-sexual-health, technology-and-ai, gut-health, mindset-and-philosophy]

guest: [full name of guest if this is an interview or podcast episode with a named expert, else "none"]

---

IMPORTANT: The Topics and guest lines MUST appear at the end exactly as shown (they are parsed by the pipeline).
"""

PDF_SUMMARY_PROMPT = """\
You are building the Immortality Wiki — a knowledge base on longevity, anti-aging, and human health optimization.

Given the text of a PDF document (research paper, book chapter, or report), produce a structured markdown summary.

Document title: {title}
Source file: {filename}

Content:
{content}

---

Output ONLY valid markdown (no code fences, no preamble) with this exact structure:

# {title}

> 📄 Source: {filename}

## Overview

[2-3 sentence overview of what this document covers and its core thesis or findings]

## Key Insights

[5-10 bullet points with the most important insights, data points, or findings. Include numbers, percentages, and study details where present.]

## Core Concepts

[Key concepts, mechanisms, or frameworks explained with 1-2 sentences each]

## Practical Takeaways

[3-7 actionable takeaways based on this document's findings]

## Key Learnings & Conclusions

[2-4 sentence synthesis — the most important conclusion from this document for a longevity knowledge base]

## Topics

topics: [comma-separated list of 1-3 most relevant topic slugs from:
diet-and-nutrition, exercise-and-fitness, sleep-and-recovery, supplements-and-protocols, biomarkers-and-testing, mental-health, longevity-science, skin-and-anti-aging, environment-and-toxins, hormones-and-sexual-health, technology-and-ai, gut-health, mindset-and-philosophy]

guest: none

---

IMPORTANT: The Topics line MUST appear at the end exactly as shown.
"""


# ── Progress ──────────────────────────────────────────────────────────────────
def load_progress():
    with open(PROGRESS_FILE) as f:
        return json.load(f)


def save_progress(data):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Transcript tiers ──────────────────────────────────────────────────────────
def _parse_vtt(path):
    vtt = open(path).read()
    parts, seen = [], set()
    for line in vtt.split("\n"):
        line = line.strip()
        if not line or line.startswith("WEBVTT") or "-->" in line or line.isdigit():
            continue
        clean = re.sub(r"<[^>]+>", "", line).strip()
        if clean and clean not in seen:
            seen.add(clean)
            parts.append(clean)
    return " ".join(parts).strip() or False


def _get_transcript_ytapi(video_id):
    """Tier 1: youtube-transcript-api — fast, no IP risk."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
        api = YouTubeTranscriptApi()
        t = api.fetch(video_id)
        text = " ".join(s["text"] for s in t.to_raw_data() if s.get("text")).strip()
        return text if text else False
    except Exception as e:
        name = type(e).__name__
        if "NoTranscript" in name or "TranscriptsDisabled" in name or "NotTranslatable" in name:
            return False
        return None


def _get_transcript_ytdlp(video_id, retries=1):
    """Tier 2: yt-dlp with cookies — retries on 429."""
    import subprocess, glob, tempfile
    url = f"https://www.youtube.com/watch?v={video_id}"
    cookies_arg = ["--cookies", str(COOKIES_FILE)] if COOKIES_FILE.exists() else []

    for attempt in range(retries):
        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = [
                "python3", "-m", "yt_dlp",
                "--write-auto-sub", "--skip-download",
                "--sub-lang", "en", "--sub-format", "vtt",
                *cookies_arg,
                "--quiet", "--no-warnings",
                "-o", f"{tmpdir}/%(id)s", url
            ]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                if attempt < retries - 1:
                    time.sleep(30)
                    continue
                return None

            stderr = result.stderr
            if "429" in stderr or "Too Many Requests" in stderr:
                if attempt < retries - 1:
                    wait = 60 * (attempt + 1)
                    print(f"    yt-dlp 429 — sleeping {wait}s (attempt {attempt+1}/{retries})")
                    time.sleep(wait)
                    continue
                return None

            if "no subtitles" in stderr.lower() or "no captions" in result.stdout.lower():
                return False

            files = glob.glob(f"{tmpdir}/*.vtt")
            if not files:
                return False
            return _parse_vtt(files[0])

    return None


def _get_transcript_playwright(video_id):
    """Tier 3: CDP → real Chrome session — native clicks bypass trusted-gesture guard."""
    import asyncio
    from playwright.async_api import async_playwright

    async def _run():
        url = f"https://www.youtube.com/watch?v={video_id}&hl=en"
        async with async_playwright() as p:
            cdp_url = "http://127.0.0.1:9223"
            use_cdp = False
            try:
                import urllib.request
                urllib.request.urlopen(f"{cdp_url}/json/version", timeout=2)
                use_cdp = True
            except Exception:
                pass

            if use_cdp:
                browser = await p.chromium.connect_over_cdp(cdp_url)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await context.new_page()
            else:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    locale="en-US",
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                )
                page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)

                # Path A: "Show transcript" in the More Actions dropdown (most channels)
                transcript_opened = False
                try:
                    # Find the first VISIBLE More actions button (index varies — sidebar has many)
                    visible_more = await page.evaluate("""() => {
                        const btns = Array.from(document.querySelectorAll('button[aria-label="More actions"]'));
                        for (let i = 0; i < btns.length; i++) {
                            const r = btns[i].getBoundingClientRect();
                            const s = window.getComputedStyle(btns[i]);
                            if (r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden') {
                                return i;
                            }
                        }
                        return -1;
                    }""")
                    if visible_more >= 0:
                        more_btn = page.locator('button[aria-label="More actions"]').nth(visible_more)
                        await more_btn.click(force=True)
                        await asyncio.sleep(1.5)
                        transcript_item = page.locator(
                            'tp-yt-paper-item, ytd-menu-service-item-renderer, yt-formatted-string'
                        ).filter(has_text="transcript").first
                        await transcript_item.wait_for(state="visible", timeout=4000)
                        await transcript_item.click(force=True)
                        transcript_opened = True
                except Exception:
                    pass

                if not transcript_opened:
                    # Path B: expand description → click "Show transcript" button there
                    # (Bryan Johnson and similar channels: button lives in description section)
                    try:
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(0.5)
                        expand_btn = page.locator('tp-yt-paper-button#expand').first
                        await expand_btn.wait_for(state="visible", timeout=5000)
                        await expand_btn.click(force=True)
                        await asyncio.sleep(1.5)
                        show_btn = page.locator('button[aria-label="Show transcript"]').first
                        await show_btn.wait_for(state="visible", timeout=5000)
                        await show_btn.scroll_into_view_if_needed()
                        await asyncio.sleep(0.3)
                        await show_btn.click()
                        transcript_opened = True
                    except Exception:
                        return None

                await asyncio.sleep(3)

                # Extract transcript text — parse "Search transcript" header from any expanded panel
                text = await page.evaluate("""() => {
                    function extractFromPanel(panel) {
                        const raw = panel.innerText || '';
                        const lines = raw.split('\\n').map(l => l.trim()).filter(l => l);
                        const out = [];
                        let pastHeader = false;
                        for (const line of lines) {
                            if (!pastHeader) {
                                if (line === 'Search transcript') { pastHeader = true; }
                                continue;
                            }
                            if (/^\\d+:\\d+(:\\d+)?$/.test(line)) continue;
                            if (/^\\d+ (second|minute)/.test(line)) continue;
                            out.push(line);
                        }
                        return out.join(' ').replace(/\\s{2,}/g, ' ').trim();
                    }

                    // Check named transcript panels first
                    for (const sel of ['[target-id="PAmodern_transcript_view"]', '[target-id="engagement-panel-searchable-transcript"]']) {
                        const p = document.querySelector(sel);
                        if (p && p.innerText.includes('Search transcript')) {
                            const t = extractFromPanel(p);
                            if (t.length > 100) return t;
                        }
                    }

                    // "In this video" combined panel (target=null, becomes EXPANDED after clicking Show transcript)
                    const expandedPanel = document.querySelector(
                        'ytd-engagement-panel-section-list-renderer[visibility="ENGAGEMENT_PANEL_VISIBILITY_EXPANDED"]'
                    );
                    if (expandedPanel && expandedPanel.innerText.includes('Search transcript')) {
                        const t = extractFromPanel(expandedPanel);
                        if (t.length > 100) return t;
                    }

                    // Old YouTube UI: ytd-transcript-segment-renderer
                    const segs = document.querySelectorAll('ytd-transcript-segment-renderer');
                    if (segs.length > 0) {
                        return Array.from(segs).map(s => {
                            const el = s.querySelector('.segment-text, [class*="segment-text"]');
                            return el ? el.innerText.trim() : s.innerText.trim();
                        }).filter(t => t.length > 0).join(' ').replace(/\\s{2,}/g, ' ').trim();
                    }

                    return '';
                }""")

                if not text or not re.search(r'\w{3,}', text):
                    return False

                text = re.sub(r'\s{2,}', ' ', text).strip()
                return text if len(text) > 200 else False

            except Exception as e:
                print(f" playwright err: {e}", end=" ")
                return None
            finally:
                await page.close()
                if not use_cdp:
                    await browser.close()

    try:
        return asyncio.run(_run())
    except Exception as e:
        print(f" playwright fatal: {e}", end=" ")
        return None


def get_transcript(video_id):
    """Three-tier transcript fetch. False=no transcript, None=error/blocked."""
    result = _get_transcript_ytapi(video_id)
    if result:
        return result

    print("    ytapi miss — trying yt-dlp...", end=" ", flush=True)
    result = _get_transcript_ytdlp(video_id)
    if result:
        return result

    print("    yt-dlp miss — trying Playwright CDP...", end=" ", flush=True)
    result = _get_transcript_playwright(video_id)
    if result:
        return result

    if result is None:
        print("    WARN: all tiers failed/blocked — leaving as pending")
        return "SKIP_KEEP_PENDING"

    return False


def extract_pdf_text(pdf_path, max_chars=60000):
    """Extract text from PDF using PyMuPDF."""
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text[:max_chars].strip()
    except ImportError:
        print("ERROR: PyMuPDF not installed. Run: pip3 install pymupdf")
        sys.exit(1)


# ── LLM ───────────────────────────────────────────────────────────────────────
def call_llm(prompt):
    """Call Claude Haiku for summarization."""
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


# ── Metadata parsing ──────────────────────────────────────────────────────────
def parse_summary_metadata(summary_text):
    topics = []
    guest = None

    topics_match = re.search(r"topics:\s*(.+)", summary_text, re.IGNORECASE)
    if topics_match:
        raw = topics_match.group(1).strip().rstrip(".")
        topics = [t.strip() for t in raw.split(",") if t.strip() in VALID_TOPICS]

    guest_match = re.search(r"guest:\s*(.+)", summary_text, re.IGNORECASE)
    if guest_match:
        val = guest_match.group(1).strip().rstrip(".").strip("*").strip()
        if val.lower() not in ("none", "n/a", "-", "", "none.", "*none*"):
            guest = val

    return topics, guest


def clean_summary(summary_text):
    """Remove ## Topics metadata footer — internal use only."""
    return re.sub(r"\n## Topics\n[\s\S]*$", "", summary_text).strip()


def extract_conclusions(summary_text):
    """Extract the Key Learnings & Conclusions section for topic pages."""
    m = re.search(r"## Key Learnings & Conclusions\n\n(.+?)(?=\n##|\Z)", summary_text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Fallback: first 2 sentences of Overview
    m2 = re.search(r"## Overview\n\n(.+?)(?=\n##|\Z)", summary_text, re.DOTALL)
    if m2:
        text = m2.group(1).strip()
        sentences = re.split(r"(?<=[.!?])\s+", text)
        return " ".join(sentences[:2]).strip()
    return ""


# ── Folder naming ─────────────────────────────────────────────────────────────
def safe_folder_name(title):
    name = title.replace("&", "and").replace("|", "-").replace("#", "").replace("%", "")
    name = re.sub(r'[<>:"/\\?*]', "", name)
    # Remove parentheses — MkDocs link parser chokes on () inside markdown URLs
    name = name.replace("(", "").replace(")", "")
    # Normalize apostrophes and ellipsis
    name = name.replace("’", "").replace("…", "...").replace("'", "")
    name = re.sub(r"\s+", " ", name).strip()
    return name


# ── File writers ──────────────────────────────────────────────────────────────
def write_transcript_md(folder, video_id, title, transcript_text):
    content = f"""---
title: "Transcript: {title}"
search:
  exclude: true
---

# Transcript: {title}

**YouTube:** [Watch on YouTube](https://www.youtube.com/watch?v={video_id})

---

{transcript_text}
"""
    (folder / "transcript.md").write_text(content, encoding="utf-8")


def write_pdf_source_md(folder, title, filename, content_text):
    source_content = f"""---
title: "Source: {title}"
search:
  exclude: true
---

# Source: {title}

**File:** {filename}

---

{content_text}
"""
    (folder / "source.md").write_text(source_content, encoding="utf-8")


def write_summary_md(folder, summary_text):
    (folder / "summary.md").write_text(summary_text, encoding="utf-8")


# ── MAP / GUESTS / Conclusions updaters ───────────────────────────────────────
def append_to_map(ep_label, title, folder_name, source_url=None):
    if source_url:
        line = f"- [{ep_label} — {title}](Episodes/{folder_name}/summary.md) — [▶]({source_url})\n"
    else:
        line = f"- [{ep_label} — {title}](Episodes/{folder_name}/summary.md)\n"
    with open(MAP_FILE, "a", encoding="utf-8") as f:
        f.write(line)


def append_to_conclusions(topic_slug, ep_label, title, folder_name, conclusions_text, source_url=None):
    topic_file = TOPIC_FILES[topic_slug]
    if source_url:
        entry = f"\n### [{ep_label} — {title}](../Episodes/{folder_name}/summary.md)\n\n{conclusions_text}\n\n[▶ Source]({source_url})\n"
    else:
        entry = f"\n### [{ep_label} — {title}](../Episodes/{folder_name}/summary.md)\n\n{conclusions_text}\n"
    with open(topic_file, "a", encoding="utf-8") as f:
        f.write(entry)


def append_to_guests(guest_name, ep_label, title, folder_name, source_url=None):
    if source_url:
        entry = f"\n## {guest_name}\n\n**Episode:** [{ep_label} — {title}](Episodes/{folder_name}/summary.md) — [▶ Watch]({source_url})\n"
    else:
        entry = f"\n## {guest_name}\n\n**Episode:** [{ep_label} — {title}](Episodes/{folder_name}/summary.md)\n"
    with open(GUESTS_FILE, "a", encoding="utf-8") as f:
        f.write(entry)


def update_index_count(new_count):
    """Update episode count in docs/index.md."""
    index_path = DOCS_DIR / "index.md"
    text = index_path.read_text(encoding="utf-8")
    text = re.sub(r'\*\*\d+\*\* episodes', f'**{new_count}** episodes', text)
    text = re.sub(r'\| \*\*Total Episodes\*\* \| \d+ \|', f'| **Total Episodes** | {new_count} |', text)
    index_path.write_text(text, encoding="utf-8")


# ── Video processor ───────────────────────────────────────────────────────────
def process_video(video, dry_run=False):
    num = video["num"]
    video_id = video["id"]
    title = video["title"]
    ep_label = f"EP-{num}"
    folder_name = f"{ep_label} - {safe_folder_name(title)}"
    folder = EPISODES_DIR / folder_name
    yt_url = f"https://www.youtube.com/watch?v={video_id}"

    print(f"\n{'='*60}")
    print(f"[{num}/131] {title}")
    print(f"  ID: {video_id} | Folder: Episodes/{folder_name}")

    if dry_run:
        print("  DRY RUN — skipping")
        return True

    # 1. Fetch transcript
    print("  Fetching transcript...", end=" ", flush=True)
    transcript = get_transcript(video_id)
    if transcript is False:
        print("NO TRANSCRIPT — marking no-transcript")
        return False
    if transcript == "SKIP_KEEP_PENDING":
        return "pending"
    print(f"OK ({len(transcript):,} chars)")

    # 2. Generate summary
    print("  Generating summary...", end=" ", flush=True)
    prompt = VIDEO_SUMMARY_PROMPT.format(
        title=title,
        video_id=video_id,
        transcript=transcript[:60000],
    )
    summary_raw = call_llm(prompt)
    print("OK")

    # 3. Parse metadata
    topics, guest = parse_summary_metadata(summary_raw)
    summary_clean = clean_summary(summary_raw)
    conclusions = extract_conclusions(summary_clean)

    print(f"  Topics: {topics or ['(none)']}")
    if guest:
        print(f"  Guest: {guest}")

    # 4. Write files
    folder.mkdir(parents=True, exist_ok=True)
    write_transcript_md(folder, video_id, title, transcript)
    write_summary_md(folder, summary_clean)
    print(f"  Written: Episodes/{folder_name}/")

    # 5. Update MAP.md
    append_to_map(ep_label, title, folder_name, yt_url)

    # 6. Update Conclusions
    for topic_slug in topics:
        append_to_conclusions(topic_slug, ep_label, title, folder_name, conclusions, yt_url)

    # 7. Update GUESTS.md if podcast guest
    if guest:
        append_to_guests(guest, ep_label, title, folder_name, yt_url)

    return True


# ── PDF processor ─────────────────────────────────────────────────────────────
def process_pdf(pdf_path, dry_run=False):
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        print(f"ERROR: File not found: {pdf_path}")
        return False

    data = load_progress()
    pdfs = data.get("pdfs", [])
    next_num = len(pdfs) + 1

    # Derive title from filename
    title = pdf_path.stem.replace("-", " ").replace("_", " ").title()
    ep_label = f"PDF-{next_num}"
    folder_name = f"{ep_label} - {safe_folder_name(title)}"
    folder = EPISODES_DIR / folder_name

    print(f"\n{'='*60}")
    print(f"[PDF-{next_num}] {title}")
    print(f"  File: {pdf_path.name} | Folder: Episodes/{folder_name}")

    if dry_run:
        print("  DRY RUN — skipping")
        return True

    # 1. Extract text
    print("  Extracting PDF text...", end=" ", flush=True)
    pdf_text = extract_pdf_text(pdf_path)
    if not pdf_text or len(pdf_text) < 100:
        print("FAIL — no readable text")
        return False
    print(f"OK ({len(pdf_text):,} chars)")

    # 2. Generate summary
    print("  Generating summary...", end=" ", flush=True)
    prompt = PDF_SUMMARY_PROMPT.format(
        title=title,
        filename=pdf_path.name,
        content=pdf_text,
    )
    summary_raw = call_llm(prompt)
    print("OK")

    # 3. Parse metadata
    topics, _ = parse_summary_metadata(summary_raw)
    summary_clean = clean_summary(summary_raw)
    conclusions = extract_conclusions(summary_clean)

    print(f"  Topics: {topics or ['(none)']}")

    # 4. Write files
    folder.mkdir(parents=True, exist_ok=True)
    write_pdf_source_md(folder, title, pdf_path.name, pdf_text)
    write_summary_md(folder, summary_clean)
    print(f"  Written: Episodes/{folder_name}/")

    # 5. Update MAP.md
    append_to_map(ep_label, title, folder_name)

    # 6. Update Conclusions
    for topic_slug in topics:
        append_to_conclusions(topic_slug, ep_label, title, folder_name, conclusions)

    # 7. Record in progress
    pdfs.append({
        "num": next_num,
        "filename": pdf_path.name,
        "title": title,
        "status": "done",
        "processed": datetime.now().strftime("%Y-%m-%d"),
    })
    data["pdfs"] = pdfs
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    save_progress(data)

    return True


# ── Channel refresh ───────────────────────────────────────────────────────────
def fetch_channel_videos():
    """Re-fetch channel and add any new videos to PROGRESS.json."""
    import subprocess
    print(f"Fetching video list from {CHANNEL_URL}...")
    result = subprocess.run(
        ["python3", "-m", "yt_dlp", "--flat-playlist",
         "--print", "%(id)s|||%(title)s", CHANNEL_URL],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        print(f"ERROR: yt-dlp failed: {result.stderr[:200]}")
        return

    data = load_progress()
    existing_ids = {v["id"] for v in data["videos"]}
    current_max = max((v["num"] for v in data["videos"]), default=0)

    new_videos = []
    for line in result.stdout.strip().split("\n"):
        parts = line.split("|||")
        if len(parts) < 2:
            continue
        vid_id = parts[0].strip()
        title = parts[1].strip()
        if vid_id not in existing_ids:
            current_max += 1
            new_videos.append({"num": current_max, "id": vid_id, "title": title, "status": "pending", "processed": None})

    if new_videos:
        data["videos"].extend(new_videos)
        data["total"] = len(data["videos"])
        data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        save_progress(data)
        print(f"Added {len(new_videos)} new video(s). Total: {data['total']}")
    else:
        print("No new videos found.")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--video", type=str, help="Process specific video ID")
    parser.add_argument("--pdf", type=str, help="Path to PDF file to process")
    parser.add_argument("--process-inbox", action="store_true", help="Process all PDFs in inbox/")
    parser.add_argument("--fetch-channel", action="store_true", help="Refresh video list from YouTube")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=3.0)
    args = parser.parse_args()

    if not ANTHROPIC_API_KEY and not args.dry_run and not args.fetch_channel:
        # Try loading from root .env
        env_path = Path(__file__).parent.parent.parent.parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip().strip('"')
                    globals()["ANTHROPIC_API_KEY"] = os.environ["ANTHROPIC_API_KEY"]
                    break
        if not ANTHROPIC_API_KEY:
            print("ERROR: ANTHROPIC_API_KEY not set.")
            sys.exit(1)

    # Handle special modes
    if args.fetch_channel:
        fetch_channel_videos()
        return

    if args.pdf:
        process_pdf(args.pdf, dry_run=args.dry_run)
        return

    if args.process_inbox:
        pdfs = sorted(INBOX_DIR.glob("*.pdf"))
        if not pdfs:
            print("No PDFs found in inbox/")
            return
        print(f"Found {len(pdfs)} PDF(s) in inbox/")
        for pdf in pdfs:
            process_pdf(pdf, dry_run=args.dry_run)
            if not args.dry_run:
                time.sleep(args.delay)
        return

    # YouTube video mode
    data = load_progress()
    videos = data["videos"]

    if args.video:
        targets = [v for v in videos if v["id"] == args.video]
        if not targets:
            print(f"Video ID {args.video} not found in PROGRESS.json. Adding as one-off...")
            targets = [{"num": 0, "id": args.video, "title": args.video, "status": "pending", "processed": None}]
    else:
        targets = [v for v in videos if v["status"] == "pending"][:args.limit]

    if not targets:
        print("No pending videos.")
        return

    print(f"Processing {len(targets)} video(s)...")
    done = skipped = 0

    for i, video in enumerate(targets):
        success = process_video(video, dry_run=args.dry_run)

        if not args.dry_run:
            if success is True:
                video["status"] = "done"
                video["processed"] = datetime.now().strftime("%Y-%m-%d")
                done += 1
            elif success == "pending":
                skipped += 1
            elif success is None:
                skipped += 1
            else:
                video["status"] = "no-transcript"
                skipped += 1
            data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
            save_progress(data)

        if i < len(targets) - 1:
            time.sleep(args.delay)

    pending = sum(1 for v in videos if v["status"] == "pending")
    print(f"\nDone. Processed: {done}, Skipped: {skipped}, Remaining: {pending}/131")


if __name__ == "__main__":
    main()
