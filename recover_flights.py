import os
import pandas as pd

src = os.path.expanduser("~/Downloads/flight_data/flights_clean.csv")
out = "data/processed/flights_clean_recovered.csv"
os.makedirs("data/processed", exist_ok=True)

total_inferred = 0
total_eligible = 0
first = True

for chunk in pd.read_csv(src, chunksize=100_000, low_memory=False):

    missing = chunk["diverted"].isna()

    infer = (
        missing
        & chunk["tz_dest_known"].fillna(False)
        & chunk["arr_dt_utc"].notna()
        & chunk["arr_time"].notna()
        & chunk["arr_delay"].notna()
        & ~chunk["arr_time_inconsistent"].fillna(False)
        & ~chunk["arrival_elapsed_inconsistent"].fillna(False)
    )

    chunk["diversion_status_inferred_from_arrival"] = infer

    chunk["is_diverted"] = (
        chunk["diverted"].eq(1).astype("boolean")
        .mask(missing, pd.NA)
    )

    chunk["is_cancelled"] = (
        chunk["cancelled"].eq(1).astype("boolean")
        .mask(chunk["cancelled"].isna(), pd.NA)
    )

    eligible = (
        chunk["valid_tail_number"].fillna(False)
        & chunk["valid_crs_dep_time"].fillna(False)
        & chunk["has_utc_timestamps"].fillna(False)
        & chunk["valid_arr_time"].fillna(False)
        & chunk["is_cancelled"].eq(False).fillna(False)
        & (
            chunk["is_diverted"].eq(False).fillna(False)
            | infer
        )
        & ~chunk["dep_time_inconsistent"].fillna(False)
        & ~chunk["arr_time_inconsistent"].fillna(False)
        & ~chunk["arrival_elapsed_inconsistent"].fillna(False)
        & chunk["dep_dst_status"].eq("ok")
        & ~chunk["implausible_record"].fillna(False)
    ).fillna(False)

    chunk["rotation_eligible"] = eligible

    total_inferred += int(infer.sum())
    total_eligible += int(eligible.sum())

    chunk.to_csv(
        out,
        mode="w" if first else "a",
        header=first,
        index=False
    )
    first = False

print("\nRECOVERY COMPLETE")
print("Inferred statuses:", total_inferred)
print("Rotation-eligible rows:", total_eligible)
print("Output:", out)
