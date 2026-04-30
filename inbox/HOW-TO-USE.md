# Immortality Wiki — PDF Inbox

Drop PDF files here. Run `python3 pipeline.py --process-inbox` to process all of them.

## Steps

1. Copy PDFs from Downloads into this folder
2. `cd Wiki/active/execution/Immortality-Wiki`
3. `python3 pipeline.py --process-inbox`

Each PDF gets:
- `docs/Episodes/PDF-N - Title/summary.md` — AI-generated summary
- `docs/Episodes/PDF-N - Title/source.md` — extracted full text (search excluded)
- Entry in `docs/MAP.md`
- Entries in relevant `docs/Conclusions/*.md` topic pages

## Individual PDF

```bash
python3 pipeline.py --pdf inbox/my-document.pdf
```

## Requirements

- `pip3 install pymupdf anthropic` (if not already installed)
