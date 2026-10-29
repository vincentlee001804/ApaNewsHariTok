# Milestone-aligned FYP2 Gantt chart — bar positions match the official
# Milestones Verification Form (FYP II) SEPT2627 weeks. Styled like the original.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.lines import Line2D

DONE = "#1d4fa0"
PLAN = "#9db9e4"
MILE = "#e8a13a"

# (group name, [ (label, [(start_wk, end_wk)]) ])
groups = [
    ("PLANNING", [
        ("Revised project plan &\nFYP1 comment responses", [(0, 1)]),
    ]),
    ("DEVELOPMENT  (complete by W4 = Milestone 3)", [
        ("Semantic dedup + pgvector RAG\nintegration & evaluation", [(1, 4)]),
        ("Real-time official utility\nalert integration", [(1, 4)]),
        ("Tuning set + NLP summary\nbenchmark (mimo)", [(2, 4)]),
    ]),
    ("DEPLOYMENT  (Milestone 4, W6)", [
        ("Production deployment & 24/7\nhosting verification (Fly.io)", [(4, 6)]),
        ("Repository URL &\ndeployment docs", [(5, 6)]),
    ]),
    ("EVALUATION  (plan W7 / data W8)", [
        ("Test planning: survey instrument,\nrecruitment & consent", [(4, 7)]),
        ("Survey distribution &\ndata collection (150+)", [(6, 8)]),
        ("Statistical analysis\n(TAM, descriptive stats)", [(7, 9)]),
    ]),
    ("REPORT & PRESENTATION", [
        ("Ch4 results + discussion\ndrafting", [(8, 11)]),
        ("Ch5 conclusion & final\nthesis assembly", [(10, 11)]),
        ("Final submission &\npresentation (M8)", [(11, 12)]),
    ]),
]

# milestone week (1-based) -> label
milestones = {1: "M1 Plan review", 3: "M2 Dev progress", 4: "M3 Impl. complete",
              6: "M4 Deployed", 7: "M5 Test plan", 8: "M6 Data collected",
              11: "M7 Ch4 discussion", 12: "M8 Submission"}

rows = []
for gname, items in groups:
    rows.append(("GROUP", gname))
    for it in items:
        rows.append(("TASK", it))

n = len(rows)
fig, ax = plt.subplots(figsize=(15, 9.6), dpi=110)
fig.patch.set_facecolor("#f5f0e6")
ax.set_facecolor("#f5f0e6")

y = n + 1  # +1 for milestone row at top
for kind, payload in rows:
    y -= 1
    if kind == "GROUP":
        ax.text(-0.15, y + 0.5, payload, fontsize=11.5, fontweight="bold",
                ha="right", va="center", color="#14202e")
        continue
    label, spans = payload
    ax.text(-0.15, y + 0.5, label, fontsize=9.5, ha="right", va="center", color="#14202e")
    for (s, e) in spans:
        ax.barh(y + 0.5, e - s, left=s, height=0.58, color=PLAN, edgecolor="none", zorder=3)

# milestone row (top)
mile_y = n + 0.5
for wk, lbl in milestones.items():
    ax.plot(wk, mile_y, marker="D", markersize=10, color=MILE, mec="white", mew=1.2, zorder=6)
    ax.annotate(lbl, (wk, mile_y), textcoords="offset points", xytext=(0, 12),
                ha="center", fontsize=7.6, color="#8a5a10", fontweight="bold", rotation=0)
ax.text(-0.15, mile_y, "MILESTONES (official form)", fontsize=11.5, fontweight="bold",
        ha="right", va="center", color="#8a5a10")

ax.set_xlim(0, 12)
ax.set_ylim(0, n + 1.6)
ax.set_xticks([i + 0.5 for i in range(12)])
ax.set_xticklabels([f"WEEK {i+1}" for i in range(12)], fontsize=8.5, fontweight="bold")
ax.set_yticks([])
for i in range(13):
    ax.axvline(i, color="#c9c2b2", lw=0.8, zorder=1)
# highlight milestone weeks lightly
for wk in milestones:
    ax.axvspan(wk - 1, wk, color="#e8a13a", alpha=0.08, zorder=0)
for spine in ax.spines.values():
    spine.set_visible(False)
ax.tick_params(length=0)

ax.set_title("FINAL YEAR PROJECT 2 — GANTT CHART (MILESTONE-ALIGNED, OFFICIAL FORM WEEKS)",
             fontsize=14.5, fontweight="bold", color="#1d4fa0", pad=14, loc="left")
legend = [Patch(facecolor=PLAN, label="Planned work"),
          Line2D([0], [0], marker="D", color="none", markerfacecolor=MILE,
                 markersize=9, label="Official milestone (form)")]
ax.legend(handles=legend, loc="upper left", bbox_to_anchor=(0.005, 1.005),
          ncol=2, frameon=False, fontsize=9.5, handletextpad=0.4, columnspacing=1.2)

plt.tight_layout(rect=[0.24, 0.02, 0.98, 0.94])
plt.savefig("fyp2_gantt_milestone_aligned.png", dpi=110, bbox_inches="tight",
            facecolor=fig.get_facecolor())
print("saved fyp2_gantt_milestone_aligned.png")
