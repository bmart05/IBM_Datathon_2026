"""Optional visual QA: python -m riverguard.data.plot_fixture (requires matplotlib)."""
from pathlib import Path
import json

from .ingestion import load_fixture
from .preprocessing import quality_report


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as dates
    import matplotlib.pyplot as plt
    import pandas as pd

    frame = load_fixture()
    report = quality_report(frame, expected_interval_minutes=15)
    output = Path(__file__).parent / "fixtures"
    # Break lines at missing intervals; never draw a continuous series over gaps.
    stages = frame.set_index("timestamp")["stage_ft"].asfreq("15min")
    fig, ax = plt.subplots(figsize=(11, 5.3), layout="constrained")
    fig.set_facecolor("#f7fafc")
    ax.plot(stages.index, stages, color="#157c9b", linewidth=2, label="USGS observed stage (approved archive)")
    ax.axhline(19, color="#bd6228", linestyle="--", linewidth=1.4, label="Minor flood stage: 19 ft (NOAA ERKM7)")
    for i, gap in enumerate(report["gaps"]):
        ax.axvspan(pd.Timestamp(gap["after"]), pd.Timestamp(gap["before"]), color="#c75959", alpha=.23,
                   label="Missing observations (unfilled)" if i == 0 else None)
    peak = frame.loc[frame.stage_ft.idxmax()]
    ax.scatter([peak.timestamp], [peak.stage_ft], color="#157c9b", zorder=4)
    ax.annotate(f"Sampled peak: {peak.stage_ft:.2f} ft\n{peak.timestamp:%d Nov %H:%M} UTC",
                xy=(peak.timestamp, peak.stage_ft), xytext=(-120, -45), textcoords="offset points",
                arrowprops={"arrowstyle": "->", "color": "#365364"})
    ax.set(title="RiverGuard data QA · Meramec River near Eureka (USGS 07019000)",
           ylabel="Gauge height (feet)", xlabel="Observation time (UTC)")
    ax.xaxis.set_major_formatter(dates.DateFormatter("%d %b", tz=dates.UTC))
    ax.grid(alpha=.16)
    ax.legend(loc="lower right", fontsize=8)
    fig.suptitle("Historical observations · 4–10 November 2024 · 571 readings · two gaps", fontsize=10, color="#365364")
    fig.savefig(output / "eureka-nov-2024.png", dpi=150)
    report["sampled_peak_ft"] = float(peak.stage_ft)
    report["sampled_peak_time"] = peak.timestamp.isoformat()
    report["retrieved_at"] = frame.attrs["retrieved_at"]
    report["source_urls"] = frame.attrs["source_urls"]
    (output / "eureka-nov-2024-quality.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
