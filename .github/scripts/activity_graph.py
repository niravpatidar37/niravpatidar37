"""Generate a GitHub activity graph SVG (last 31 days) in a tokyo-night style.

Self-hosted replacement for github-readme-activity-graph.vercel.app.
Runs in GitHub Actions; needs GITHUB_TOKEN and GH_USER in the environment.
Usage: python activity_graph.py dist/activity-graph.svg

The y-axis is log-scaled (log1p, so zero days still sit on the baseline) so a
single busy day doesn't flatten the rest of the month; the peak day is labelled.
"""
import json
import math
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

DAYS = 31
W, H = 1000, 380
PAD_L, PAD_R, PAD_T, PAD_B = 70, 30, 70, 60

THEME = {
    "bg": "#1a1b27",
    "title": "#70a5fd",
    "text": "#9aa5ce",
    "grid": "#2a2e42",
    "line": "#bf91f3",
    "point": "#38bdae",
    "area": "#bf91f3",
}

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def fetch(user: str, token: str) -> list[tuple[date, int]]:
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=DAYS - 1)
    body = json.dumps({
        "query": QUERY,
        "variables": {
            "login": user,
            "from": f"{start}T00:00:00Z",
            "to": f"{today}T23:59:59Z",
        },
    }).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    if "errors" in payload:
        raise SystemExit(f"GraphQL error: {payload['errors']}")
    weeks = payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    counts = {
        date.fromisoformat(d["date"]): d["contributionCount"]
        for w in weeks for d in w["contributionDays"]
    }
    return [(start + timedelta(days=i), counts.get(start + timedelta(days=i), 0)) for i in range(DAYS)]


# Candidate axis ticks; roughly evenly spaced on a log1p scale.
TICKS = (0, 1, 3, 10, 30, 100, 300, 1000, 3000, 10000)
# Axis-top candidates: a finer ladder so the top isn't far above the peak.
TOPS = (4, 5, 10, 15, 20, 30, 50, 75, 100, 150, 200, 300, 500, 750, 1000,
        1500, 2000, 3000, 5000, 7500, 10000)
HEADROOM = 1.08  # peak sits at <= ~93% of the plot height, leaving room for its label


def log_top(peak: int) -> int:
    """Smallest axis top whose log1p is at least HEADROOM x log1p(peak)."""
    need = math.log1p(peak) * HEADROOM
    for t in TOPS:
        if math.log1p(t) >= need:
            return t
    return math.ceil(math.expm1(need))


def axis_ticks(top: int, min_gap: float = 0.08) -> list[int]:
    """Ticks below `top` plus `top`, dropping any closer than `min_gap` (fraction of height) to the next one."""
    span = math.log1p(top)
    ticks = [top]
    for t in sorted((t for t in TICKS if t < top), reverse=True):
        if (math.log1p(ticks[-1]) - math.log1p(t)) / span >= min_gap:
            ticks.append(t)
    return sorted(ticks)


def peak_index(data: list[tuple[date, int]]) -> int:
    """Index of the highest day; ties go to the most recent one."""
    best = 0
    for i, (_, c) in enumerate(data):
        if c >= data[best][1]:
            best = i
    return best


def render(user: str, data: list[tuple[date, int]]) -> str:
    t = THEME
    cw, ch = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    peak = max(c for _, c in data)
    ymax = log_top(peak)
    span = math.log1p(ymax)
    n = len(data)

    def x(i):
        return PAD_L + cw * i / (n - 1)

    def y(v):
        return PAD_T + ch - ch * math.log1p(v) / span

    pts = [(x(i), y(c)) for i, (_, c) in enumerate(data)]

    # smooth path (Catmull-Rom -> cubic Bezier)
    def smooth(points):
        d = f"M{points[0][0]:.1f},{points[0][1]:.1f}"
        for i in range(len(points) - 1):
            p0 = points[i - 1] if i else points[i]
            p1, p2 = points[i], points[i + 1]
            p3 = points[i + 2] if i + 2 < len(points) else p2
            c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
            c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
            c1 = (c1[0], min(c1[1], PAD_T + ch))
            c2 = (c2[0], min(c2[1], PAD_T + ch))
            d += f" C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}"
        return d

    line = smooth(pts)
    area = f"{line} L{pts[-1][0]:.1f},{PAD_T + ch} L{pts[0][0]:.1f},{PAD_T + ch} Z"

    grid, ylabels = [], []
    for v in axis_ticks(ymax):
        gy = y(v)
        grid.append(f'<line x1="{PAD_L}" y1="{gy:.1f}" x2="{W - PAD_R}" y2="{gy:.1f}" stroke="{t["grid"]}" stroke-dasharray="4 4"/>')
        ylabels.append(f'<text x="{PAD_L - 12}" y="{gy + 4:.1f}" text-anchor="end">{int(v)}</text>')

    xlabels = []
    for i, (d, _) in enumerate(data):
        if i % 3 == 0 or i == n - 1:
            xlabels.append(f'<text x="{x(i):.1f}" y="{PAD_T + ch + 22}" text-anchor="middle">{d.day}</text>')

    dots = "".join(
        f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" fill="{t["point"]}"><title>{d.isoformat()}: {c}</title></circle>'
        for (px, py), (d, c) in zip(pts, data)
    )
    total = sum(c for _, c in data)

    peak_svg = ""
    if peak > 0:
        pi = peak_index(data)
        (px, py), (pd, pc) = pts[pi], data[pi]
        anchor = "end" if px > W - PAD_R - 80 else "start" if px < PAD_L + 80 else "middle"
        dx = -8 if anchor == "end" else 8 if anchor == "start" else 0
        label = f"{pc} on {pd.strftime('%b')} {pd.day}"
        peak_svg = (
            f'<circle cx="{px:.1f}" cy="{py:.1f}" r="7" fill="none" stroke="{t["title"]}" stroke-width="2"/>'
            f'<text x="{px + dx:.1f}" y="{py - 12:.1f}" text-anchor="{anchor}" class="peak">Peak: {label}</text>'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="{user}'s contribution graph">
  <defs>
    <linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{t['area']}" stop-opacity="0.35"/>
      <stop offset="100%" stop-color="{t['area']}" stop-opacity="0.02"/>
    </linearGradient>
  </defs>
  <style>
    text {{ font-family: 'Segoe UI', Ubuntu, 'Helvetica Neue', sans-serif; fill: {t['text']}; font-size: 12px; }}
    .title {{ fill: {t['title']}; font-size: 20px; font-weight: 600; }}
    .sub {{ font-size: 13px; }}
    .peak {{ fill: {t['title']}; font-size: 13px; font-weight: 600; }}
  </style>
  <rect width="{W}" height="{H}" rx="8" fill="{t['bg']}"/>
  <text x="{W / 2}" y="36" text-anchor="middle" class="title">{user}'s Contribution Graph</text>
  <text x="{W / 2}" y="56" text-anchor="middle" class="sub">{total} contributions in the last {DAYS} days · log scale</text>
  {''.join(grid)}
  <g>{''.join(ylabels)}</g>
  <g>{''.join(xlabels)}</g>
  <path d="{area}" fill="url(#fill)"/>
  <path d="{line}" fill="none" stroke="{t['line']}" stroke-width="2.5" stroke-linecap="round"/>
  {dots}
  {peak_svg}
  <text x="{W / 2}" y="{H - 10}" text-anchor="middle">Days</text>
  <text x="20" y="{PAD_T + ch / 2}" text-anchor="middle" transform="rotate(-90 20 {PAD_T + ch / 2})">Contributions (log)</text>
</svg>
"""


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "activity-graph.svg"
    user = os.environ["GH_USER"]
    if os.environ.get("MOCK"):
        import random
        today = date.today()
        data = [(today - timedelta(days=DAYS - 1 - i), random.choice([0, 0, 1, 2, 3, 5, 8, 12])) for i in range(DAYS)]
    else:
        data = fetch(user, os.environ["GITHUB_TOKEN"])
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(user, data))
    print(f"wrote {out}")
