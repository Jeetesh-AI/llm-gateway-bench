"""Generates dark and light theme SVG charts from measured load test JSON."""

import json
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
RESULTS_JSON = ROOT / "docs" / "results" / "load_test.json"
IMG_DIR = ROOT / "docs" / "img"

THEMES = {
    "light": {
        "bg": "#ffffff",
        "card": "#f8fafc",
        "border": "#cbd5e1",
        "text": "#0f172a",
        "muted": "#475569",
        "grid": "#e2e8f0",
        "accent": "#6d28d9",
        "accent2": "#0284c7",
        "good": "#16a34a",
    },
    "dark": {
        "bg": "#0d1117",
        "card": "#161b22",
        "border": "#30363d",
        "text": "#e6edf3",
        "muted": "#8b949e",
        "grid": "#21262d",
        "accent": "#a78bfa",
        "accent2": "#38bdf8",
        "good": "#4ade80",
    },
}


def build_degradation_svg(levels: List[Dict[str, object]], theme_name: str) -> str:
    t = THEMES[theme_name]
    w, h = 760, 360
    pad_l, pad_r, pad_t, pad_b = 70, 36, 56, 56
    pw = w - pad_l - pad_r
    ph = h - pad_t - pad_b

    xs = [float(r["concurrent_users"]) for r in levels]
    ys = [float(r["slo_attainment_pct"]) for r in levels]
    min_x, max_x = min(xs), max(xs)

    def map_x(val: float) -> float:
        return pad_l + ((val - min_x) / max(1.0, (max_x - min_x))) * pw

    def map_y(pct: float) -> float:
        return pad_t + ph - (pct / 100.0) * ph

    points = " ".join(f"{map_x(x):.1f},{map_y(y):.1f}" for x, y in zip(xs, ys))

    grid_lines = []
    for pct in [0, 25, 50, 75, 100]:
        gy = map_y(pct)
        grid_lines.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{w - pad_r}" y2="{gy:.1f}" '
            f'stroke="{t["grid"]}" stroke-width="1" />'
            f'<text x="{pad_l - 10}" y="{gy + 4:.1f}" text-anchor="end" '
            f'fill="{t["muted"]}" font-size="11" font-family="ui-sans-serif, system-ui">{pct}%</text>'
        )

    x_labels = []
    dots = []
    for r in levels:
        cx = map_x(float(r["concurrent_users"]))
        cy = map_y(float(r["slo_attainment_pct"]))
        x_labels.append(
            f'<text x="{cx:.1f}" y="{h - pad_b + 20}" text-anchor="middle" '
            f'fill="{t["muted"]}" font-size="11" font-family="ui-sans-serif, system-ui">'
            f'{r["concurrent_users"]}u</text>'
        )
        dots.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="{t["accent"]}" '
            f'stroke="{t["bg"]}" stroke-width="2" />'
            f'<text x="{cx:.1f}" y="{cy - 10:.1f}" text-anchor="middle" '
            f'fill="{t["text"]}" font-size="11" font-weight="600" '
            f'font-family="ui-sans-serif, system-ui">{r["slo_attainment_pct"]}%</text>'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
  <rect width="{w}" height="{h}" rx="12" fill="{t['bg']}" stroke="{t['border']}" />
  <text x="{pad_l}" y="30" fill="{t['text']}" font-size="16" font-weight="700" font-family="ui-sans-serif, system-ui">
    SLO Attainment vs. Concurrent Chat Users (First Token &lt;= 1.5s, Retries Included)
  </text>
  {''.join(grid_lines)}
  <line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{h - pad_b}" stroke="{t['border']}" stroke-width="1.5" />
  <line x1="{pad_l}" y1="{h - pad_b}" x2="{w - pad_r}" y2="{h - pad_b}" stroke="{t['border']}" stroke-width="1.5" />
  <polyline fill="none" stroke="{t['accent']}" stroke-width="3" points="{points}" />
  {''.join(dots)}
  {''.join(x_labels)}
  <text x="{pad_l + pw / 2:.1f}" y="{h - 14}" text-anchor="middle" fill="{t['muted']}" font-size="12" font-family="ui-sans-serif, system-ui">
    Concurrent Users
  </text>
</svg>
"""


def build_architecture_svg(theme_name: str) -> str:
    t = THEMES[theme_name]
    w, h = 760, 250
    boxes = [
        (30, 75, 130, 80, "Clients", "OpenAI SDK + Retries", t["accent2"]),
        (210, 55, 190, 120, "FastAPI Gateway", "Auth · Budgets · Fairness\nAdmission Cap (10 slots)", t["accent"]),
        (455, 75, 135, 80, "LLM Engine", "Streaming / vLLM\nNever Queues Blindly", t["good"]),
        (630, 45, 105, 65, "SQLite Ledger", "Token Usage", t["muted"]),
        (630, 130, 105, 65, "Prometheus", "TTFT & 503s", t["muted"]),
    ]
    nodes = []
    for x, y, bw, bh, title, sub, col in boxes:
        lines = sub.split("\n")
        sub_svg = "".join(
            f'<text x="{x + bw / 2:.1f}" y="{y + 44 + i * 15}" text-anchor="middle" '
            f'fill="{t["muted"]}" font-size="11" font-family="ui-sans-serif, system-ui">{ln}</text>'
            for i, ln in enumerate(lines)
        )
        nodes.append(
            f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="10" fill="{t["card"]}" '
            f'stroke="{col}" stroke-width="2" />'
            f'<text x="{x + bw / 2:.1f}" y="{y + 24}" text-anchor="middle" '
            f'fill="{t["text"]}" font-size="13" font-weight="700" font-family="ui-sans-serif, system-ui">{title}</text>'
            f'{sub_svg}'
        )

    arrows = [
        (160, 115, 210, 115),
        (400, 115, 455, 115),
        (400, 90, 630, 77),
        (400, 140, 630, 162),
    ]
    arrow_svg = "".join(
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{t["muted"]}" stroke-width="2" marker-end="url(#arr)" />'
        for x1, y1, x2, y2 in arrows
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
  <defs>
    <marker id="arr" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
      <path d="M0,0 L0,6 L6,3 z" fill="{t['muted']}" />
    </marker>
  </defs>
  <rect width="{w}" height="{h}" rx="12" fill="{t['bg']}" stroke="{t['border']}" />
  <text x="30" y="32" fill="{t['text']}" font-size="15" font-weight="700" font-family="ui-sans-serif, system-ui">
    Gateway-Enforced Admission &amp; Observability Architecture
  </text>
  {arrow_svg}
  {''.join(nodes)}
</svg>
"""


def main() -> None:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    levels = data["levels"]

    for theme in ("light", "dark"):
        (IMG_DIR / f"degradation-{theme}.svg").write_text(
            build_degradation_svg(levels, theme), encoding="utf-8"
        )
        (IMG_DIR / f"architecture-{theme}.svg").write_text(
            build_architecture_svg(theme), encoding="utf-8"
        )
    print(f"Generated 4 SVG charts in {IMG_DIR}")


if __name__ == "__main__":
    main()
