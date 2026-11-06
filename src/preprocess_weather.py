from pathlib import Path
import glob
import pandas as pd

INPUT_DIR = Path("data/weather/2024")
OUTPUT = Path("data/processed/weather_observations_2024.csv")

VARIABLES = [
    "temperature",
    "dew_point_temperature",
    "wind_speed",
    "precipitation",
    "relative_humidity",
    "visibility",
    "sea_level_pressure",
]

ACCEPTED = {
    "343": {"1", "5", "A"},
    "223": {"1"},
}

parts = []
files = sorted(glob.glob(str(INPUT_DIR / "*.psv.gz")))

for i, path in enumerate(files, 1):
    for chunk in pd.read_csv(
        path, sep="|", compression="gzip",
        chunksize=100_000, low_memory=False
    ):
        chunk["DATE"] = pd.to_datetime(
            chunk["DATE"], errors="coerce", utc=True
        )
        chunk["STATION"] = chunk["STATION"].astype(str)
        chunk["station_source"] = None

        for col in VARIABLES:
            if col not in chunk:
                continue

            source_col = f"{col}_Source_Code"
            quality_col = f"{col}_Quality_Code"

            if source_col not in chunk or quality_col not in chunk:
                chunk[col] = pd.NA
                continue

            source = pd.to_numeric(
                chunk[source_col], errors="coerce"
            ).astype("Int64").astype("string")

            quality = (
                chunk[quality_col].astype("string")
                .str.strip().str.replace(r"\.0$", "", regex=True)
            )

            value = pd.to_numeric(chunk[col], errors="coerce")

            if col == "relative_humidity":
                # NOAA marks derived RH with f (suspect/error)
                # or o (out of range). Blank QC is retained.
                valid = ~quality.isin(["f", "o"])
                valid &= source.isin(["343", "223"])
            else:
                valid = pd.Series(False, index=chunk.index)
                for src, codes in ACCEPTED.items():
                    valid |= (source == src) & quality.isin(codes)

            chunk[col] = value.where(valid)

        keep = ["STATION", "DATE", *VARIABLES]
        parts.append(chunk[keep].dropna(subset=["STATION", "DATE"]))

    print(f"[{i}/{len(files)}] {Path(path).name}")

weather = pd.concat(parts, ignore_index=True)
del parts

weather = weather.sort_values(["STATION", "DATE"])
weather = weather.drop_duplicates(
    ["STATION", "DATE"], keep="last"
).reset_index(drop=True)

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
weather.to_csv(OUTPUT, index=False)

print("\nSOURCE-AWARE WEATHER QC COMPLETE")
print("Rows:", f"{len(weather):,}")
print("Stations:", weather.STATION.nunique())
print("Date range:", weather.DATE.min(), "to", weather.DATE.max())
print("\nRetained measurement percentages:")
print(weather[VARIABLES].notna().mean().mul(100).round(2))
print("\nSaved:", OUTPUT)
