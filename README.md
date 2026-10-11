# flight-stats-crawler

An educational scraper that collects airport departures from
[FlightStats](https://www.flightstats.com/v2/flight-tracker/departures/MHD/).
It covers every day and 6-hour window the site has data for, writes the flights
to a CSV file, and prints live progress and a summary report in the terminal.

No browser or Selenium is needed. The flight data is embedded in each page as
JSON (`__NEXT_DATA__`), so plain HTTP requests are enough.

## Setup

Requires Python 3.13+.

### With uv

```bash
uv sync
```

### With venv + pip

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install httpx fire rich
```

## Usage

```bash
uv run main.py AIRPORT [AIRPORT ...] [flags]
# or, inside the activated venv:
python main.py AIRPORT [AIRPORT ...] [flags]
```

| Argument            | Default          | Description                                              |
|---------------------|------------------|----------------------------------------------------------|
| `AIRPORT ...`       | required         | IATA codes, separated by spaces or commas (`MHD BGW` or `MHD,BGW`) |
| `--output`          | `departures.csv` | Path of the CSV file to write                            |
| `--max_past_days`   | `14`             | How many days back from today to check                   |
| `--max_future_days` | `7`              | How many days ahead of today to check                    |
| `--min_delay`       | `1.0`            | Minimum random delay between requests (seconds)          |
| `--max_delay`       | `4.0`            | Maximum random delay between requests (seconds)          |

The crawler starts at today and walks backward and forward one day at a time.
In each direction it stops at the first date the site has no data for, or at
the max-days limit. Each day is fetched in four windows: 00–06, 06–12, 12–18
and 18–24.

### Examples

```bash
# All four airports, full available range
uv run main.py MHD BGW NJF EBL

# Comma-separated, custom output file
uv run main.py MHD,BGW --output=iran_iraq.csv

# Only today and the next 2 days
uv run main.py NJF --max_past_days=0 --max_future_days=2

# Slower, more polite crawling
uv run main.py EBL --min_delay=3 --max_delay=8

# Show built-in help
uv run main.py -- --help
```

## Output

One CSV row per flight:

| Column             | Example                      | Meaning                                                      |
|--------------------|------------------------------|--------------------------------------------------------------|
| `airport`          | `NJF`                        | Departure airport you asked for                              |
| `date`             | `2026-10-11`                 | Departure date                                               |
| `hour_window`      | `06:00-12:00`                | 6-hour window of the page the flight was found on            |
| `departure_time`   | `07:30`                      | Scheduled departure, local time at the departure airport     |
| `arrival_time`     | `08:00`                      | Scheduled arrival, local time at the destination             |
| `sort_time_utc`    | `2026-10-11T04:30:00.000Z`   | Same departure as a full UTC timestamp; use it to sort and compare across time zones |
| `carrier_code`     | `IA`                         | Airline code used by FlightStats: usually IATA, sometimes 3-letter or a site-specific code like `JI*` |
| `carrier_name`     | `Iraqi Airways`              | Airline name                                                 |
| `flight_number`    | `450`                        | Flight number; with `carrier_code` it forms the flight ID (`IA 450`) |
| `destination_code` | `BGW`                        | Destination airport code                                     |
| `destination_city` | `Baghdad`                    | Destination city                                             |
| `operated_by`      | *(usually empty)*            | Operating airline for codeshare flights, as raw JSON         |
| `url`              | `https://www.flightstats.com/v2/flight-tracker/IA/450?...` | Flight detail page (status, gates, delays) |
