"""FlightStats departures scraper (educational).

Usage:
    python main.py MHD BGW NJF EBL
    python main.py MHD,BGW --output=out.csv --max_past_days=3
"""

import csv
import json
import random
import time
from collections import Counter, defaultdict
from datetime import date, timedelta

import fire
import httpx
from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

BASE_URL = "https://www.flightstats.com/v2/flight-tracker/departures/{airport}/"
HOURS = (0, 6, 12, 18)  # the site serves departures in 6-hour windows
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
CSV_FIELDS = [
    "airport",
    "date",
    "hour_window",
    "departure_time",
    "arrival_time",
    "sort_time_utc",
    "carrier_code",
    "carrier_name",
    "flight_number",
    "destination_code",
    "destination_city",
    "operated_by",
    "url",
]
HEAT_STYLES = ("grey50", "green4", "green3", "bright_green", "bold yellow")

console = Console()


def window_label(hour: int) -> str:
    return f"{hour:02d}-{(hour + 6) % 24:02d}"


def fetch_route(client: httpx.Client, airport: str, day: date, hour: int) -> dict | None:
    """Return the `route` object from the page's __NEXT_DATA__, or None if the
    date is outside the range the site serves."""
    params = {"year": day.year, "month": day.month, "date": day.day, "hour": hour}
    resp = client.get(BASE_URL.format(airport=airport), params=params)
    resp.raise_for_status()

    marker = "__NEXT_DATA__ = "
    start = resp.text.find(marker)
    if start == -1:
        raise ValueError("__NEXT_DATA__ not found in page")
    data, _ = json.JSONDecoder().raw_decode(resp.text, start + len(marker))

    route = data["props"]["initialState"]["flightTracker"].get("route") or {}
    # Dates outside the available range come back without a header.
    if not route.get("header"):
        return None
    return route


def to_rows(airport: str, day: date, hour: int, route: dict) -> list[dict]:
    rows = []
    for f in route.get("flights") or []:
        carrier = f.get("carrier") or {}
        dest = f.get("airport") or {}
        operated_by = f.get("operatedBy")
        rows.append(
            {
                "airport": airport,
                "date": day.isoformat(),
                "hour_window": f"{hour:02d}:00-{(hour + 6) % 24:02d}:00",
                "departure_time": (f.get("departureTime") or {}).get("time24"),
                "arrival_time": (f.get("arrivalTime") or {}).get("time24"),
                "sort_time_utc": f.get("sortTime"),
                "carrier_code": carrier.get("fs"),
                "carrier_name": carrier.get("name"),
                "flight_number": carrier.get("flightNumber"),
                "destination_code": dest.get("fs"),
                "destination_city": dest.get("city"),
                "operated_by": json.dumps(operated_by) if operated_by else "",
                "url": "https://www.flightstats.com/v2" + f["url"] if f.get("url") else "",
            }
        )
    return rows


def parse_airports(airports: tuple) -> list[str]:
    # Fire turns "MHD,BGW" into a tuple, so flatten whatever we get.
    codes = []
    for a in airports:
        items = a if isinstance(a, (list, tuple)) else str(a).split(",")
        codes.extend(str(i).strip().upper() for i in items if str(i).strip())
    return list(dict.fromkeys(codes))


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def bar(value: int, maximum: int, width: int = 16) -> Text:
    filled = round(value / maximum * width) if maximum else 0
    return Text("█" * filled, style="cyan") + Text("░" * (width - filled), style="grey23")


def heat_cell(value: int, maximum: int) -> Text:
    if not value:
        return Text("·", style="grey35")
    level = min(len(HEAT_STYLES) - 1, int(value / maximum * (len(HEAT_STYLES) - 1) + 0.5))
    return Text(str(value), style=HEAT_STYLES[level])


def summary_table(rows: list[dict], codes: list[str], pages: Counter, errors: list) -> Table:
    table = Table(title="Summary by airport", box=box.ROUNDED, header_style="bold magenta")
    table.add_column("Airport", style="bold cyan")
    table.add_column("Date range")
    table.add_column("Pages", justify="right")
    table.add_column("Flights", justify="right", style="bold green")
    table.add_column("Destinations", justify="right")
    table.add_column("Carriers", justify="right")
    table.add_column("Busiest day")
    table.add_column("Errors", justify="right")

    error_count = Counter(e[0] for e in errors)
    for code in codes:
        ap_rows = [r for r in rows if r["airport"] == code]
        days = Counter(r["date"] for r in ap_rows)
        busiest = days.most_common(1)
        table.add_row(
            code,
            f"{min(days)} → {max(days)}" if days else "-",
            str(pages[code]),
            str(len(ap_rows)),
            str(len({r["destination_code"] for r in ap_rows})),
            str(len({r["carrier_code"] for r in ap_rows})),
            f"{busiest[0][0]} ({busiest[0][1]})" if busiest else "-",
            Text(str(error_count[code]), style="red" if error_count[code] else "dim"),
        )
    table.add_section()
    table.add_row(
        "TOTAL",
        "",
        str(sum(pages.values())),
        str(len(rows)),
        str(len({r["destination_code"] for r in rows})),
        str(len({r["carrier_code"] for r in rows})),
        "",
        Text(str(len(errors)), style="red" if errors else "dim"),
        style="bold",
    )
    return table


def heatmap_table(code: str, rows: list[dict]) -> Table:
    counts = defaultdict(Counter)
    for r in rows:
        if r["airport"] == code:
            counts[r["date"]][r["hour_window"][:2]] += 1

    table = Table(title=f"{code} · flights per window", box=box.SIMPLE_HEAD, header_style="bold")
    table.add_column("Date", style="cyan")
    for h in HOURS:
        table.add_column(window_label(h), justify="center")
    table.add_column("Total", justify="right", style="bold")

    peak = max((c for day in counts.values() for c in day.values()), default=0)
    for day in sorted(counts):
        cells = [heat_cell(counts[day][f"{h:02d}"], peak) for h in HOURS]
        table.add_row(day, *cells, str(sum(counts[day].values())))
    return table


def top_table(title: str, counter: Counter, label: str, limit: int = 10) -> Table:
    table = Table(title=title, box=box.SIMPLE_HEAD, header_style="bold magenta")
    table.add_column("#", justify="right", style="dim")
    table.add_column(label)
    table.add_column("Flights", justify="right", style="bold")
    table.add_column("")
    top = counter.most_common(limit)
    peak = top[0][1] if top else 0
    for i, (name, n) in enumerate(top, 1):
        table.add_row(str(i), name, str(n), bar(n, peak))
    return table


def print_report(rows, codes, pages, errors, elapsed, requests, output) -> None:
    console.print()
    console.rule("[bold magenta]✈  Departures report")
    console.print()
    console.print(summary_table(rows, codes, pages, errors), justify="center")

    heatmaps = [heatmap_table(c, rows) for c in codes if any(r["airport"] == c for r in rows)]
    if heatmaps:
        grid = Table.grid(padding=(0, 4))
        for i in range(0, len(heatmaps), 2):
            grid.add_row(*heatmaps[i : i + 2])
        console.print(Panel(grid, title="Activity heatmap", border_style="blue"), justify="center")

    destinations = Counter(f"{r['destination_code']} · {r['destination_city']}" for r in rows)
    carriers = Counter(f"{r['carrier_code']} · {r['carrier_name']}" for r in rows)
    top_tables = (
        top_table("Top destinations", destinations, "Destination"),
        top_table("Top carriers", carriers, "Carrier"),
    )
    # Side by side on wide terminals, stacked otherwise.
    if console.width >= 140:
        tops = Table.grid(padding=(0, 4))
        tops.add_row(*top_tables)
        console.print(tops, justify="center")
    else:
        for t in top_tables:
            console.print(t, justify="center")

    if errors:
        err = Table(title="Errors", box=box.SIMPLE_HEAD, header_style="bold red")
        for col in ("Airport", "Date", "Window", "Error"):
            err.add_column(col)
        for code, day, hour, msg in errors:
            err.add_row(code, str(day), window_label(hour), msg)
        console.print(err, justify="center")

    minutes, seconds = divmod(int(elapsed), 60)
    stats = Text.assemble(
        ("Flights ", "dim"), (f"{len(rows)}", "bold green"),
        ("   Requests ", "dim"), (f"{requests}", "bold"),
        ("   Errors ", "dim"), (f"{len(errors)}", "bold red" if errors else "bold"),
        ("   Elapsed ", "dim"), (f"{minutes}m {seconds:02d}s", "bold"),
        ("   Saved to ", "dim"), (output, "bold underline cyan"),
    )
    console.print(Panel(stats, border_style="green", title="Done", expand=False), justify="center")


# --------------------------------------------------------------------------- #
# Crawler
# --------------------------------------------------------------------------- #


def crawl(
    *airports: str,
    output: str = "departures.csv",
    max_past_days: int = 14,
    max_future_days: int = 7,
    min_delay: float = 1.0,
    max_delay: float = 4.0,
) -> None:
    """Scrape departures for the given airports across all available days and
    hour windows, and write them to a CSV file.

    Args:
        airports: IATA codes, e.g. MHD BGW NJF EBL (or comma-separated).
        output: CSV file path.
        max_past_days: How far back to probe before giving up.
        max_future_days: How far ahead to probe before giving up.
        min_delay: Minimum random delay between requests (seconds).
        max_delay: Maximum random delay between requests (seconds).
    """
    codes = parse_airports(airports)
    if not codes:
        raise ValueError("Provide at least one airport code, e.g. `python main.py MHD BGW`")

    today = date.today()
    console.print(
        Panel(
            Group(
                Text.assemble(("Airports  ", "dim"), (" ".join(codes), "bold cyan")),
                Text.assemble(
                    ("Range     ", "dim"),
                    (f"{today - timedelta(days=max_past_days)} → {today + timedelta(days=max_future_days)}", "bold"),
                    ("  (stops early when the site has no data)", "dim"),
                ),
                Text.assemble(("Delay     ", "dim"), (f"{min_delay:g}–{max_delay:g}s random", "bold")),
                Text.assemble(("Output    ", "dim"), (output, "bold")),
            ),
            title="[bold magenta]✈  FlightStats departures crawler",
            border_style="magenta",
            expand=False,
        )
    )

    all_rows: list[dict] = []
    pages: Counter = Counter()
    errors: list[tuple] = []
    requests = 0
    started = time.monotonic()

    # Each direction has a known maximum of slots; when a direction stops early
    # we shrink the progress totals so the bars stay accurate.
    directions = ((-1, 0, max_past_days), (1, 1, max_future_days))
    slots_per_airport = sum((last - first + 1) * len(HOURS) for _, first, last in directions)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(bar_width=30),
        MofNCompleteColumn(),
        TextColumn("[green]{task.fields[flights]} flights"),
        TimeElapsedColumn(),
        TextColumn("eta"),
        TimeRemainingColumn(),
        console=console,
    )

    with (
        progress,
        httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client,
        open(output, "w", newline="", encoding="utf-8") as fh,
    ):
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()

        overall = progress.add_task("[bold]Overall", total=slots_per_airport * len(codes), flights=0)
        status = progress.add_task("", total=None, flights="", visible=True)
        airport_tasks = {
            c: progress.add_task(f"  [cyan]{c}", total=slots_per_airport, flights=0, start=False)
            for c in codes
        }

        def set_status(msg: str) -> None:
            progress.update(status, description=f"  [dim]{msg}")

        for code in codes:
            task = airport_tasks[code]
            progress.start_task(task)
            ap_flights = 0
            progress.console.print(
                Text(f"{code:<4}{'date':<12}" + "  ".join(window_label(h) for h in HOURS), style="bold dim")
            )

            for step, first, last in directions:
                direction_slots = (last - first + 1) * len(HOURS)
                done = 0
                for offset in range(first, last + 1):
                    day = today + timedelta(days=offset * step)
                    per_window = []
                    available = True

                    for hour in HOURS:
                        if requests:
                            delay = random.uniform(min_delay, max_delay)
                            set_status(f"sleeping {delay:.1f}s before {code} {day} {window_label(hour)}")
                            time.sleep(delay)
                        set_status(f"fetching {code} {day} {window_label(hour)}")

                        requests += 1
                        try:
                            route = fetch_route(client, code, day, hour)
                        except (httpx.HTTPError, ValueError) as exc:
                            errors.append((code, day, hour, str(exc)[:80]))
                            per_window.append(Text("  err", style="red"))
                            route = {}
                        else:
                            if route is None:
                                available = False
                                break
                            rows = to_rows(code, day, hour, route)
                            writer.writerows(rows)
                            fh.flush()
                            all_rows.extend(rows)
                            pages[code] += 1
                            ap_flights += len(rows)
                            per_window.append(
                                Text(f"{len(rows):>5}", style="green" if rows else "grey35")
                            )

                        done += 1
                        progress.update(task, advance=1, flights=ap_flights)
                        progress.update(overall, advance=1, flights=len(all_rows))

                    if per_window:
                        day_total = sum(int(t.plain) for t in per_window if t.plain.strip().isdigit())
                        line = Text.assemble((f"{code} ", "bold cyan"), (f"{day}  ", ""))
                        for t in per_window:
                            line += t + Text("  ")
                        line += Text(f"= {day_total}", style="bold")
                        progress.console.print(line)

                    if not available:
                        progress.console.print(
                            Text(f"{code} {day}  no data from here on, stopping this direction", style="yellow")
                        )
                        break

                # Remove the slots we will never fetch from the totals.
                skipped = direction_slots - done
                if skipped:
                    progress.update(task, total=progress.tasks[task].total - skipped)
                    progress.update(overall, total=progress.tasks[overall].total - skipped)

            progress.update(task, description=f"  [green]✔ {code}")

        progress.update(status, visible=False)

    print_report(all_rows, codes, pages, errors, time.monotonic() - started, requests, output)


if __name__ == "__main__":
    fire.Fire(crawl)
