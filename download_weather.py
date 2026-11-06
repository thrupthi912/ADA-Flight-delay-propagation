
import csv
import gzip
import time
import urllib.request
import urllib.error
from pathlib import Path

CSV_FILE = Path("airport_weather_station_matches.csv")
OUTPUT_DIR = Path("data/weather/2024")
BASE_URL = (
    "https://www.ncei.noaa.gov/oa/global-historical-climatology-network/"
    "hourly/access/by-year/2024/psv/GHCNh_{station}_2024.psv"
)

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

with CSV_FILE.open(newline="") as f:
    stations = sorted({
        row["station_id"].strip()
        for row in csv.DictReader(f)
        if row.get("station_id", "").strip()
    })

def download_station(station):
    output = OUTPUT_DIR / f"GHCNh_{station}_2024.psv.gz"
    url = BASE_URL.format(station=station)

    if output.exists() and output.stat().st_size > 0:
        try:
            with gzip.open(output, "rb") as f:
                if f.read(1):
                    return "SKIPPED"
        except OSError:
            output.unlink()

    for attempt in range(3):
        temp = output.with_suffix(output.suffix + ".tmp")
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "WeatherResearch/1.0"}
            )
            with urllib.request.urlopen(request, timeout=90) as response:
                with gzip.open(temp, "wb") as compressed:
                    while chunk := response.read(1024 * 1024):
                        compressed.write(chunk)

            with gzip.open(temp, "rb") as check:
                if not check.read(1):
                    raise ValueError("Empty station file")

            temp.replace(output)
            return "DOWNLOADED"

        except Exception as error:
            temp.unlink(missing_ok=True)
            if attempt == 2:
                return f"FAILED: {error}"
            time.sleep(2 ** attempt)

    return "FAILED"

if __name__ == "__main__":
    import sys

    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(stations)
    results = []

    for index, station in enumerate(stations[:limit], 1):
        status = download_station(station)
        results.append((station, status))
        print(f"[{index}/{limit}] {station}: {status}")

    downloaded = sum(s == "DOWNLOADED" for _, s in results)
    skipped = sum(s == "SKIPPED" for _, s in results)
    failed = sum(s.startswith("FAILED") for _, s in results)

    print("\nSummary")
    print("Downloaded:", downloaded)
    print("Skipped:", skipped)
    print("Failed:", failed)

    with open("weather_download_log.csv", "a", newline="") as f:
        writer = csv.writer(f)
        for station, status in results:
            writer.writerow([station, status])
