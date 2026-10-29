# Generates the revised FYP2 Gantt chart (blue-bar style matching the original).
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.lines import Line2D

DONE = "#1d4fa0"     # dark blue  - completed
PLAN = "#9db9e4"     # light blue - planned
MILE = "#e8a13a"     # orange     - milestone

# (label, [(start_week, end_week_exclusive, done?)], [milestone_weeks])
groups = [
    ("FOUNDATION (DONE WK1)", [
        ("Infra: Supabase + Oracle migration", [(0, 1, True)], []),
        ("P1: Summary-length consistency",     [(0, 1, True)], []),
        ("P2: pgvector RAG + hybrid retrieval",[(0, 1, True)], []),
        ("P3: Semantic dedup (+ tuning set)",  [(0, 1, True), (1, 2, False)], []),
    ]),
    ("DEVELOPMENT", [
        ("Hygiene: DB password rotation,\nhosting go/no-go", [(1, 3, False)], []),
        ("P4: Real-time official utility alerts\n(time-boxed)", [(1, 4, False)], [4]),
        ("P5a: mimo summary benchmark\n(4 NLP dimensions)", [(2, 5, False)], []),
    ]),
    ("EVALUATION (150+ RESPONDENTS)", [
        ("P5b: Survey instrument +\nrecruitment prep", [(3, 5, False)], []),
        ("P5c: Survey distribution &\ncollection window", [(4, 8, False)], []),
        ("P5d: Data analysis (TAM,\ndescriptive stats)", [(7, 9, False)], []),
    ]),
    ("REPORT & PRESENTATION", [
        ("Ch1/Ch2 refinement (background,\nscope, lit review)", [(5, 7, False)], []),
        ("Ch3 methodology + Ch4\nresults write-up", [(6, 9, False)], []),
        ("Initial report submission", [(7, 9, False)], [9]),
        ("Final report drafting", [(8, 11, False)], []),
        ("Finalize, submit + slides", [(10, 12, False)], [11]),
    ]),
]

rows = []
for gname, items in groups:
    rows.append(("GROUP", gname))
    for it in items:
        rows.append(("TASK", it))

n = len(rows)
fig, ax = plt.subplots(figsize=(15, 9.2), dpi=110)
fig.patch.set_facecolor("#f5f0e6")
ax.set_facecolor("#f5f0e6")

y = n  # current row top
for kind, payload in rows:
    y -= 1
    if kind == "GROUP":
        ax.text(-0.15, y + 0.5, payload, fontsize=11.5, fontweight="bold",
                ha="right", va="center", color="#14202e")
        continue
    label, spans, miles = payload
    ax.text(-0.15, y + 0.5, label, fontsize=9.5, ha="right", va="center",
            color="#14202e")
    for (s, e, d) in spans:
        ax.barh(y + 0.5, e - s, left=s, height=0.58,
                color=DONE if d else PLAN, edgecolor="none", zorder=3)
    for m in miles:
        ax.plot(m, y + 0.5, marker="D", markersize=9, color=MILE,
                mec="white", mew=1.2, zorder=5)

ax.set_xlim(0, 12)
ax.set_ylim(0, n)
ax.set_xticks([i + 0.5 for i in range(12)])
ax.set_xticklabels([f"WEEK {i+1}" for i in range(12)], fontsize=8.5,
                   fontweight="bold")
ax.set_yticks([])
for i in range(13):
    ax.axvline(i, color="#c9c2b2", lw=0.8, zorder=1)
for spine in ax.spines.values():
    spine.set_visible(False)
ax.tick_params(length=0)

ax.set_title("FINAL YEAR PROJECT 2  —  GANTT CHART (REVISED, AS OF 10 OCT 2026)",
             fontsize=15, fontweight="bold", color="#1d4fa0", pad=14, loc="left")

legend = [Patch(facecolor=DONE, label="Completed"),
          Patch(facecolor=PLAN, label="Planned"),
          Line2D([0], [0], marker="D", color="none", markerfacecolor=MILE,
                 markersize=9, label="Milestone")]
ax.legend(handles=legend, loc="upper left", bbox_to_anchor=(0.005, 1.005),
          ncol=3, frameon=False, fontsize=9.5, handletextpad=0.4,
          columnspacing=1.2)

plt.tight_layout(rect=[0.24, 0.02, 0.98, 0.95])
plt.savefig("fyp2_gantt_revised.png", dpi=110, bbox_inches="tight",
            facecolor=fig.get_facecolor())
print("saved fyp2_gantt_revised.png")
