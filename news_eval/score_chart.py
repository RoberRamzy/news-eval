"""Render a standalone SVG from an evaluation scorecard."""
import logging
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

log = logging.getLogger(__name__)
OUTCOMES = (("passed", "Passed", "#08786d"), ("failed", "Failed", "#bc422e"),
            ("unscored", "Unscored", "#697386"))


def write_chart(report, path):
    overall = report["overall"]
    entries = report["breakdown"]
    sections = [
        ("Overall", [("All cases", overall)]),
        ("By query language", [(key.split(":", 1)[1], value) for key, value in entries.items()
                               if key.startswith("language:")]),
        ("By query → evidence language", [(key.split(":", 1)[1].replace("->", " → "), value)
                                           for key, value in entries.items()
                                           if key.startswith("language_pair:")]),
        ("By question type", [(key.split(":", 1)[1].replace("_", " "), value)
                              for key, value in entries.items() if key.startswith("type:")]),
    ]
    sections = [(title, rows) for title, rows in sections if rows]
    height = 281 + sum(38 + 36 * len(rows) for _, rows in sections)
    svg = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "width": "960",
                          "height": str(height), "viewBox": f"0 0 960 {height}",
                          "role": "img", "aria-labelledby": "title desc",
                          "font-family": "system-ui, sans-serif"})
    SubElement(svg, "title", id="title").text = "News RAG evaluation scores"
    SubElement(svg, "desc", id="desc").text = "Stacked bars show passed, failed, and unscored cases."

    def rect(x, y, width, height, fill, radius=0):
        return SubElement(svg, "rect", x=str(x), y=str(y), width=str(width), height=str(height),
                          fill=fill, rx=str(radius))

    def label(x, y, value, size=14, fill="#202b3c", weight=400):
        SubElement(svg, "text", x=str(x), y=str(y), fill=fill,
                   attrib={"font-size": str(size), "font-weight": str(weight)}).text = value

    def percent(value):
        return f"{value:.1%}" if value is not None else "Unavailable"

    rect(0, 0, 960, height, "#f1f5f9")
    rect(22, 22, 916, height - 44, "#ffffff", 16)
    label(48, 64, "News RAG evaluation scores", size=28, weight=700)
    label(48, 92, f"{report['mode'].capitalize()} evaluation  •  {overall['total']} cases", fill="#46566a")
    k = overall["context_k"]
    latency = overall["p95_latency_ms"]
    metrics = [("End-to-end pass rate", percent(overall["pass_rate"])),
               (f"Hit@{k}", percent(overall["hit_at_k"])),
               (f"Recall@{k}", percent(overall["recall_at_k"])),
               ("p95 latency", f"{latency:.2f} ms" if latency is not None else "Unavailable")]
    for index, (name, value) in enumerate(metrics):
        x = 48 + 220 * index
        rect(x, 110, 202, 68, "#eef4f8", 8)
        label(x + 12, 132, name, size=13, fill="#46566a")
        label(x + 12, 158, value, size=20, weight=700)

    for index, (_, name, color) in enumerate(OUTCOMES):
        x = 48 + 116 * index
        rect(x, 197, 12, 12, color, 2)
        label(x + 19, 208, name, size=13)
    label(526, 208, f"Retrieval metrics use {overall['retrieval_count']} chunk-labeled cases.",
          size=13, fill="#46566a")
    y = 235
    for title, rows in sections:
        label(48, y, title, size=16, weight=700)
        y += 20
        for name, stats in rows:
            label(48, y + 14, name if len(name) <= 26 else name[:25] + "…", size=13)
            x = 258
            rect(x, y, 390, 16, "#e2e8f0", 3)
            for key, title, color in OUTCOMES:
                count = stats[key]
                if count and stats["total"]:
                    width = 390 * count / stats["total"]
                    bar = rect(x, y, width, 16, color)
                    SubElement(bar, "title").text = f"{name}: {count} {title.lower()}"
                    x += width
            counts = " / ".join(str(stats[key]) for key, _, _ in OUTCOMES)
            label(667, y + 14, f"{counts}  •  {percent(stats['pass_rate'])}", size=13)
            y += 36
        y += 18
    label(48, y + 5, "Counts are ordered as passed / failed / unscored. Rates for incomplete groups are unavailable.",
          size=12, fill="#46566a")
    Path(path).write_text(tostring(svg, encoding="unicode") + "\n", encoding="utf-8")
    log.info("Wrote score graph to %s", path)
