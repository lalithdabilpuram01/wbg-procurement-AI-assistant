"""Module 1 discovery (READ-ONLY, local files only): token-size profile (cl100k) of text blocks (paragraph proxies) and pages
in the representative narrative PDFs, to ground chunk-size and overlap recommendations. Measurement only; no chunks stored."""
from __future__ import annotations

import json
from pathlib import Path

import fitz
import tiktoken

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "discovery/samples/documents/files"
ENC = tiktoken.get_encoding("cl100k_base")
FILES = ["P131049_PAD_17555469.pdf", "P145502_PAD_19368603.pdf", "P131049_ICR_32160299.pdf", "P145502_ICR_33841581.pdf", "P131049_ICRR_32800471.pdf",
         "P131049_ISR_27391778.pdf", "P127203_ICRFR_33407968.pdf"]


def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * len(v)))] if v else None


def main() -> None:
    out = {}
    for f in FILES:
        doc = fitz.open(D / f)
        blocks, pages = [], []
        for pg in doc:
            pages.append(len(ENC.encode(pg.get_text("text"))))
            for b in pg.get_text("blocks"):
                t = b[4].strip()
                if len(t) > 40:
                    blocks.append(len(ENC.encode(t)))
        out[f] = {"pages": doc.page_count, "doc_tokens": sum(pages), "page_tokens_p50": q(pages, 0.5), "page_tokens_p90": q(pages, 0.9),
                  "blocks": len(blocks), "block_tokens_p50": q(blocks, 0.5), "block_tokens_p90": q(blocks, 0.9), "block_tokens_max": max(blocks) if blocks else None}
        doc.close()
    (Path(__file__).resolve().parent / "evidence" / "token_size_profile.json").write_text(json.dumps(out, indent=1))
    for k, v in out.items():
        print(k, v)


if __name__ == "__main__":
    main()
