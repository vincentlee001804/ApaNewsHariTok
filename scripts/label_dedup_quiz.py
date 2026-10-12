"""
FYP2 dedup tuning-set labeler — quiz UI for the human-labeled threshold evidence.

Reads benchmark/dedup_tuning_set_<date>.csv (from export_dedup_tuning_set.py) and walks
you through each pair one screen at a time: article A vs article B, three big answer
buttons (or keys 1/2/3), optional note, progress bar. Labels save back to the SAME csv
incrementally, so you can quit anytime and resume later — already-labeled rows are
skipped on startup.

Run:  python scripts/label_dedup_quiz.py
"""

from __future__ import annotations

import csv
import glob
import sys
import tkinter as tk
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = PROJECT_ROOT / "docs" / "fyp2" / "benchmark"


def find_csv() -> Path:
    candidates = sorted(glob.glob(str(BENCH_DIR / "dedup_tuning_set_*.csv")), reverse=True)
    if not candidates:
        sys.exit(f"No tuning set found under {BENCH_DIR}. Run export_dedup_tuning_set.py first.")
    return Path(candidates[0])


class Quiz(tk.Tk):
    def __init__(self, csv_path: Path):
        super().__init__()
        self.csv_path = csv_path
        self.title(f"Dedup tuning labeler — {csv_path.name}")
        self.geometry("1100x760")

        with csv_path.open(newline="", encoding="utf-8") as fh:
            self.rows = list(csv.DictReader(fh))
            self.fieldnames = list(self.rows[0].keys()) if self.rows else []
        self.pending = [i for i, r in enumerate(self.rows) if not (r.get("label") or "").strip()]
        self.pos = 0

        self._build()
        if not self.pending:
            self._finish()
        else:
            self._show()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        self.progress = tk.Label(self, font=("Segoe UI", 11), anchor="w")
        self.progress.pack(fill="x", padx=12, pady=(10, 0))

        meta = tk.Label(self, font=("Segoe UI", 10), fg="#555555", anchor="w")
        self.meta = meta
        self.meta.pack(fill="x", padx=12)

        panes = tk.PanedWindow(self, orient="horizontal", sashwidth=6)
        panes.pack(fill="both", expand=True, padx=12, pady=8)

        self.text_a = self._article_pane(panes, "ARTICLE A", "#eef4fb")
        self.text_b = self._article_pane(panes, "ARTICLE B", "#f3fbf0")

        bottom = tk.Frame(self)
        bottom.pack(fill="x", padx=12, pady=(0, 12))

        tk.Label(bottom, text="Note (optional):", font=("Segoe UI", 9)).pack(side="left")
        self.note = tk.Entry(bottom, font=("Segoe UI", 10), width=40)
        self.note.pack(side="left", padx=6)
        self.note.bind("<Return>", lambda _e: self._answer("same_story"))

        btns = tk.Frame(self)
        btns.pack(fill="x", padx=12, pady=(0, 14))
        for label, key, val, color in [
            ("[1] Same story", "1", "same_story", "#2e7d32"),
            ("[2] Different story", "2", "different_story", "#c62828"),
            ("[3] Unsure", "3", "unsure", "#f9a825"),
        ]:
            b = tk.Button(
                btns, text=label, font=("Segoe UI", 12, "bold"), bg=color, fg="white",
                width=18, command=lambda v=val: self._answer(v),
            )
            b.pack(side="left", padx=8, expand=True, fill="x")
            self.bind(key, lambda _e, v=val: self._answer(v))
        self.bind("s", lambda _e: self._answer("same_story"))
        self.bind("d", lambda _e: self._answer("different_story"))
        self.bind("u", lambda _e: self._answer("unsure"))

    def _article_pane(self, parent, banner, bg):
        frame = tk.Frame(parent, bg=bg, bd=1, relief="solid")
        tk.Label(frame, text=banner, font=("Segoe UI", 11, "bold"), bg=bg,
                 anchor="w").pack(fill="x", padx=8, pady=(6, 0))
        text = tk.Text(frame, wrap="word", font=("Segoe UI", 10), bg=bg, relief="flat")
        text.pack(fill="both", expand=True, padx=8, pady=6)
        text.config(state="disabled")
        parent.add(frame, stretch="always", minsize=300)
        return text

    # ------------------------------------------------------------- logic
    def _set_text(self, widget, title: str, body: str, source: str, date: str) -> None:
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", f"{title}\n", "title")
        widget.insert("end", f"{source}  ·  {date}\n\n", "meta")
        widget.insert("end", body)
        widget.tag_config("title", font=("Segoe UI", 12, "bold"))
        widget.tag_config("meta", font=("Segoe UI", 9), foreground="#555555")
        widget.config(state="disabled")

    def _show(self) -> None:
        idx = self.pending[self.pos]
        r = self.rows[idx]
        done = len([r for r in self.rows if (r.get("label") or "").strip()])
        self.progress.config(
            text=f"Pair {self.pos + 1} of {len(self.pending)} remaining   "
                 f"({done}/{len(self.rows)} total done)   —   {r['pair_id']}"
        )
        self.meta.config(
            text=f"stratum: {r['stratum']}   ·   cosine: {r['cosine']}   ·   "
                 f"threshold gap: {r['threshold_gap']}   ·   location: {r['location']}"
        )
        self._set_text(self.text_a, r["title_a"], r["summary_a"], r["source_a"], r["date_a"])
        self._set_text(self.text_b, r["title_b"], r["summary_b"], r["source_b"], r["date_b"])
        self.note.delete(0, "end")
        if (r.get("notes") or "").strip():
            self.note.insert(0, r["notes"])

    def _answer(self, label: str) -> None:
        idx = self.pending[self.pos]
        self.rows[idx]["label"] = label
        note = self.note.get().strip()
        if note:
            self.rows[idx]["notes"] = note
        self._save()
        self.pos += 1
        if self.pos >= len(self.pending):
            self._finish()
        else:
            self._show()

    def _save(self) -> None:
        with self.csv_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=self.fieldnames)
            w.writeheader()
            w.writerows(self.rows)

    def _finish(self) -> None:
        for w in self.winfo_children():
            w.destroy()
        tk.Label(
            self, text="All pairs labeled — nice work!", font=("Segoe UI", 16, "bold"),
        ).pack(expand=True)
        tk.Label(
            self,
            text=f"Labels saved to:\n{self.csv_path}\n\nTell Kimi you're done to fold them into the doc 03 threshold analysis.",
            font=("Segoe UI", 11), justify="center",
        ).pack(expand=True)
        self._save()


if __name__ == "__main__":
    Quiz(find_csv()).mainloop()
