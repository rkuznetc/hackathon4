"""Snapshot features for ML training and runtime (past-only at inference)."""

from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

EVENT_DAYS = {
    pd.Timestamp("2026-06-14").date(),
    pd.Timestamp("2026-07-04").date(),
    pd.Timestamp("2026-07-18").date(),
    pd.Timestamp("2026-07-25").date(),
    pd.Timestamp("2026-08-08").date(),
    pd.Timestamp("2026-08-22").date(),
    pd.Timestamp("2026-08-29").date(),
    pd.Timestamp("2026-06-28").date(),
}

TARGET_COLUMNS = {
    "spend_next_7d",
    "spend_next_30d",
    "trips_next_7d",
    "trips_next_30d",
    "debt_next_7d",
}
CATEGORICAL_FEATURES = ["estimated_segment_from_history"]


def preprocess_trips(trips: pd.DataFrame) -> pd.DataFrame:
    t = trips.copy()
    if t.empty:
        return t
    t["duration_min"] = (t["exited_at"] - t["entered_at"]).dt.total_seconds() / 60
    t["date"] = t["entered_at"].dt.normalize()
    t["dow"] = t["entered_at"].dt.weekday
    t["hour"] = t["entered_at"].dt.hour
    t["is_weekend"] = t["dow"].ge(5).astype(int)
    t["is_morning"] = t["hour"].between(7, 9).astype(int)
    t["is_evening"] = t["hour"].between(16, 19).astype(int)
    t["is_short"] = t["duration_min"].lt(20).astype(int)
    t["is_long"] = t["duration_min"].gt(180).astype(int)
    t["is_zero"] = t["trip_amount"].eq(0).astype(int)
    t["is_event_day"] = t["entered_at"].dt.date.isin(EVENT_DAYS).astype(int)
    return t


def future_sum_by_group(df: pd.DataFrame, col: str, horizon: int) -> pd.Series:
    out = np.zeros(len(df), dtype=float)
    for _, idx in df.groupby("vehicle_id").indices.items():
        idx = np.asarray(idx)
        vals = df.iloc[idx][col].to_numpy(dtype=float)
        cs = np.concatenate([[0.0], np.cumsum(vals)])
        n = len(vals)
        res = np.zeros(n, dtype=float)
        for i in range(n):
            j = min(n, i + horizon + 1)
            res[i] = cs[j] - cs[i + 1]
        out[idx] = res
    return pd.Series(out, index=df.index)


def event_count_next(d: pd.Timestamp, horizon: int) -> Tuple[int, int]:
    start = d.date()
    end = (d + pd.Timedelta(days=horizon)).date()
    fut = sorted([x for x in EVENT_DAYS if start < x <= end])
    if not fut:
        return 0, 999
    return len(fut), min((x - start).days for x in fut)


def infer_segment_from_snapshot(row: Dict[str, float]) -> str:
    if row.get("total_trips_to_date", 0) <= 4 and row.get("days_since_registration", 0) <= 30:
        return "new_user"
    if row.get("w30_trips", 0) >= 90 and row.get("w30_avg_duration", 0) <= 45 and row.get("w30_short_share", 0) >= 0.65:
        return "taxi_driver"
    if row.get("w30_morning_share", 0) >= 0.35 and row.get("w30_avg_duration", 0) >= 240 and row.get("w30_trips", 0) >= 12:
        return "commuter"
    if row.get("w30_weekend_share", 0) >= 0.55 and row.get("w30_trips", 0) <= 35:
        return "weekend_guest"
    if row.get("w30_trips", 0) <= 20 and row.get("w30_avg_duration", 0) >= 90:
        return "tourist"
    if row.get("w30_short_share", 0) >= 0.75:
        return "taxi_driver"
    return "tourist" if row.get("w30_avg_duration", 0) >= 120 else "weekend_guest"


def _build_daily_feature_grid(
    vehicles: pd.DataFrame,
    trips: pd.DataFrame,
    tx: pd.DataFrame,
    *,
    include_targets: bool,
) -> pd.DataFrame:
    t = preprocess_trips(trips)
    vsmall = vehicles[["vehicle_id", "registered_at"]].copy()
    vsmall["registered_date"] = pd.to_datetime(vsmall["registered_at"]).dt.normalize()

    if t.empty:
        min_date = vsmall["registered_date"].min()
        max_date = pd.Timestamp.utcnow().normalize()
    else:
        min_date = t["entered_at"].min().normalize()
        max_date = t["entered_at"].max().normalize()

    vids = vehicles["vehicle_id"].astype(int).sort_values().to_numpy()
    dates = pd.date_range(min_date, max_date, freq="D")
    grid = pd.MultiIndex.from_product([vids, dates], names=["vehicle_id", "date"]).to_frame(index=False)

    if t.empty:
        trip_daily = pd.DataFrame(
            columns=[
                "vehicle_id",
                "date",
                "daily_trips",
                "daily_spend",
                "daily_duration_sum",
                "daily_weekend",
                "daily_morning",
                "daily_evening",
                "daily_short",
                "daily_long",
                "daily_zero",
                "daily_event",
            ]
        )
    else:
        trip_daily = t.groupby(["vehicle_id", "date"]).agg(
            daily_trips=("trip_id", "count"),
            daily_spend=("trip_amount", "sum"),
            daily_duration_sum=("duration_min", "sum"),
            daily_weekend=("is_weekend", "sum"),
            daily_morning=("is_morning", "sum"),
            daily_evening=("is_evening", "sum"),
            daily_short=("is_short", "sum"),
            daily_long=("is_long", "sum"),
            daily_zero=("is_zero", "sum"),
            daily_event=("is_event_day", "sum"),
        ).reset_index()

    df = grid.merge(trip_daily, on=["vehicle_id", "date"], how="left")
    daily_trip_cols = [c for c in df.columns if c.startswith("daily_")]
    df[daily_trip_cols] = df[daily_trip_cols].fillna(0)
    df = df.sort_values(["vehicle_id", "date"]).reset_index(drop=True)
    df["daily_active"] = (df["daily_trips"] > 0).astype(int)

    roll_cols = [
        "daily_trips",
        "daily_spend",
        "daily_duration_sum",
        "daily_weekend",
        "daily_morning",
        "daily_evening",
        "daily_short",
        "daily_long",
        "daily_zero",
        "daily_event",
        "daily_active",
    ]
    for days, prefix in [(7, "w7"), (14, "w14"), (30, "w30")]:
        r = (
            df.groupby("vehicle_id")[roll_cols]
            .rolling(window=days, min_periods=1)
            .sum()
            .reset_index(level=0, drop=True)
        )
        df[f"{prefix}_trips"] = r["daily_trips"].astype(int)
        df[f"{prefix}_spend"] = r["daily_spend"]
        df[f"{prefix}_active_days"] = r["daily_active"].astype(int)
        df[f"{prefix}_avg_amount"] = np.divide(
            r["daily_spend"],
            r["daily_trips"],
            out=np.zeros(len(df)),
            where=r["daily_trips"].to_numpy() != 0,
        )
        df[f"{prefix}_avg_duration"] = np.divide(
            r["daily_duration_sum"],
            r["daily_trips"],
            out=np.zeros(len(df)),
            where=r["daily_trips"].to_numpy() != 0,
        )
        for name, src in [
            ("weekend_share", "daily_weekend"),
            ("morning_share", "daily_morning"),
            ("evening_share", "daily_evening"),
            ("short_share", "daily_short"),
            ("long_share", "daily_long"),
            ("zero_share", "daily_zero"),
            ("event_share", "daily_event"),
        ]:
            df[f"{prefix}_{name}"] = np.divide(
                r[src],
                r["daily_trips"],
                out=np.zeros(len(df)),
                where=r["daily_trips"].to_numpy() != 0,
            )
        df[f"{prefix}_trips_per_active_day"] = np.divide(
            r["daily_trips"],
            r["daily_active"],
            out=np.zeros(len(df)),
            where=r["daily_active"].to_numpy() != 0,
        )

    df["total_trips_to_date"] = df.groupby("vehicle_id")["daily_trips"].cumsum().astype(int)
    df["total_spend_to_date"] = df.groupby("vehicle_id")["daily_spend"].cumsum()
    df["trip_date_if_active"] = df["date"].where(df["daily_trips"] > 0)
    df["last_trip_date"] = df.groupby("vehicle_id")["trip_date_if_active"].ffill()
    df["days_since_last_trip"] = (df["date"] - df["last_trip_date"]).dt.days.fillna(999).astype(int)

    x = tx.copy()
    if not x.empty:
        x["date"] = x["occurred_at"].dt.normalize()
        x["is_topup"] = x["operation_type"].isin(["topup_manual", "topup_autopay"]).astype(int)
        x["topup_amount"] = x["amount"].where(x["is_topup"].eq(1), 0.0)
        x["is_fine"] = x["operation_type"].eq("fine_assessed").astype(int)
        x["is_charge"] = x["operation_type"].eq("trip_charge").astype(int)
        x["is_negative_balance"] = x["balance_after"].lt(0).astype(int)
        tx_daily = x.groupby(["vehicle_id", "date"]).agg(
            daily_topup_count=("is_topup", "sum"),
            daily_topup_sum=("topup_amount", "sum"),
            daily_fine_count=("is_fine", "sum"),
            daily_charge_count=("is_charge", "sum"),
            daily_negative_balance_events=("is_negative_balance", "sum"),
        ).reset_index()
    else:
        tx_daily = pd.DataFrame(
            columns=[
                "vehicle_id",
                "date",
                "daily_topup_count",
                "daily_topup_sum",
                "daily_fine_count",
                "daily_charge_count",
                "daily_negative_balance_events",
            ]
        )

    df = df.merge(tx_daily, on=["vehicle_id", "date"], how="left")
    for c in [
        "daily_topup_count",
        "daily_topup_sum",
        "daily_fine_count",
        "daily_charge_count",
        "daily_negative_balance_events",
    ]:
        df[c] = df[c].fillna(0)

    tx_roll_cols = [
        "daily_topup_count",
        "daily_topup_sum",
        "daily_fine_count",
        "daily_charge_count",
        "daily_negative_balance_events",
    ]
    for days, prefix in [(7, "tx7"), (30, "tx30")]:
        r = (
            df.groupby("vehicle_id")[tx_roll_cols]
            .rolling(window=days, min_periods=1)
            .sum()
            .reset_index(level=0, drop=True)
        )
        df[f"{prefix}_topup_count"] = r["daily_topup_count"].astype(int)
        df[f"{prefix}_topup_sum"] = r["daily_topup_sum"]
        df[f"{prefix}_avg_topup"] = np.divide(
            r["daily_topup_sum"],
            r["daily_topup_count"],
            out=np.zeros(len(df)),
            where=r["daily_topup_count"].to_numpy() != 0,
        )
        df[f"{prefix}_fine_count"] = r["daily_fine_count"].astype(int)
        df[f"{prefix}_negative_balance_events"] = r["daily_negative_balance_events"].astype(int)

    if not x.empty:
        last_bal = (
            x.sort_values("occurred_at")
            .groupby(["vehicle_id", "date"])
            .tail(1)[["vehicle_id", "date", "balance_after"]]
        )
        df = df.merge(last_bal, on=["vehicle_id", "date"], how="left")
    else:
        df["balance_after"] = np.nan
    df["balance_at_snapshot"] = df.groupby("vehicle_id")["balance_after"].ffill().fillna(0)

    if not x.empty:
        topup_days = (
            x[x["operation_type"].isin(["topup_manual", "topup_autopay"])]
            .groupby(["vehicle_id", "date"])
            .size()
            .rename("topup_day")
            .reset_index()
        )
        fine_days = (
            x[x["operation_type"].eq("fine_assessed")]
            .groupby(["vehicle_id", "date"])
            .size()
            .rename("fine_day")
            .reset_index()
        )
        df = df.merge(topup_days, on=["vehicle_id", "date"], how="left").merge(
            fine_days, on=["vehicle_id", "date"], how="left"
        )
    else:
        df["topup_day"] = 0
        df["fine_day"] = 0

    df["topup_date_if"] = df["date"].where(df["topup_day"].fillna(0) > 0)
    df["fine_date_if"] = df["date"].where(df["fine_day"].fillna(0) > 0)
    df["last_topup_date"] = df.groupby("vehicle_id")["topup_date_if"].ffill()
    df["last_fine_date"] = df.groupby("vehicle_id")["fine_date_if"].ffill()
    df["days_since_last_topup"] = (df["date"] - df["last_topup_date"]).dt.days.fillna(999).astype(int)
    df["days_since_last_fine"] = (df["date"] - df["last_fine_date"]).dt.days.fillna(999).astype(int)

    if not x.empty:
        ap = (
            x[x["operation_type"].eq("topup_autopay")]
            .groupby(["vehicle_id", "date"])
            .size()
            .rename("ap")
            .reset_index()
        )
        sp = (
            x[x["operation_type"].eq("subscription_purchase")]
            .groupby(["vehicle_id", "date"])
            .size()
            .rename("sp")
            .reset_index()
        )
        df = df.merge(ap, on=["vehicle_id", "date"], how="left").merge(
            sp, on=["vehicle_id", "date"], how="left"
        )
    else:
        df["ap"] = 0
        df["sp"] = 0

    df["autopay_seen_to_date"] = (
        df.groupby("vehicle_id")["ap"].transform(lambda s: s.fillna(0).gt(0).cummax()).astype(int)
    )
    df["subscription_purchase_seen_to_date"] = (
        df.groupby("vehicle_id")["sp"].transform(lambda s: s.fillna(0).gt(0).cummax()).astype(int)
    )

    df = df.merge(vsmall[["vehicle_id", "registered_date"]], on="vehicle_id", how="left")
    df["days_since_registration"] = (
        (df["date"] - df["registered_date"]).dt.days.clip(lower=0).fillna(0).astype(int)
    )
    df[["event_days_next_7d", "days_to_next_event_7d"]] = df["date"].apply(
        lambda d: pd.Series(event_count_next(d, 7))
    )
    df[["event_days_next_30d", "days_to_next_event_30d"]] = df["date"].apply(
        lambda d: pd.Series(event_count_next(d, 30))
    )

    if include_targets:
        df["spend_next_7d"] = future_sum_by_group(df, "daily_spend", 7)
        df["spend_next_30d"] = future_sum_by_group(df, "daily_spend", 30)
        df["trips_next_7d"] = future_sum_by_group(df, "daily_trips", 7).astype(int)
        df["trips_next_30d"] = future_sum_by_group(df, "daily_trips", 30).astype(int)
        df["daily_debt_event"] = (
            (df["daily_fine_count"] > 0) | (df["daily_negative_balance_events"] > 0)
        ).astype(int)
        df["debt_next_7d"] = (future_sum_by_group(df, "daily_debt_event", 7) > 0).astype(int)

    return df


def build_snapshot_features(
    vehicles: pd.DataFrame,
    trips: pd.DataFrame,
    tx: pd.DataFrame,
    *,
    include_targets: bool = True,
) -> pd.DataFrame:
    """Weekly training snapshots (features + optional targets)."""
    df = _build_daily_feature_grid(vehicles, trips, tx, include_targets=include_targets)
    if df.empty:
        return df

    min_date = df["date"].min()
    max_date = df["date"].max()
    start = min_date + pd.Timedelta(days=14)
    end = max_date - pd.Timedelta(days=7)
    if start > end:
        snapshot_dates = pd.DatetimeIndex([max_date])
    else:
        snapshot_dates = pd.date_range(start, end, freq="7D")

    snap = df[df["date"].isin(snapshot_dates)].copy()
    # registered_date уже в daily grid (_build_daily_feature_grid); повторный merge
    # давал registered_date_x / registered_date_y и ломал фильтр ниже.
    if "registered_date" not in snap.columns:
        reg = vehicles[["vehicle_id", "registered_at"]].copy()
        reg["registered_date"] = pd.to_datetime(reg["registered_at"]).dt.normalize()
        snap = snap.merge(reg[["vehicle_id", "registered_date"]], on="vehicle_id", how="left")
    else:
        snap["registered_date"] = pd.to_datetime(snap["registered_date"]).dt.normalize()
    snap = snap[snap["date"] >= snap["registered_date"]]
    snap["snapshot_at"] = snap["date"] + pd.Timedelta(hours=23, minutes=59, seconds=59)
    snap["estimated_segment_from_history"] = snap.apply(
        lambda r: infer_segment_from_snapshot(r.to_dict()), axis=1
    )

    keep = [
        "vehicle_id",
        "snapshot_at",
        "days_since_registration",
        "total_trips_to_date",
        "total_spend_to_date",
        "days_since_last_trip",
    ]
    keep += [c for c in snap.columns if c.startswith(("w7_", "w14_", "w30_", "tx7_", "tx30_"))]
    keep += [
        "balance_at_snapshot",
        "days_since_last_topup",
        "days_since_last_fine",
        "autopay_seen_to_date",
        "subscription_purchase_seen_to_date",
        "event_days_next_7d",
        "days_to_next_event_7d",
        "event_days_next_30d",
        "days_to_next_event_30d",
        "estimated_segment_from_history",
    ]
    if include_targets:
        keep += [
            "spend_next_7d",
            "spend_next_30d",
            "trips_next_7d",
            "trips_next_30d",
            "debt_next_7d",
        ]
    snap = snap[[c for c in keep if c in snap.columns]].sort_values(
        ["snapshot_at", "vehicle_id"]
    ).reset_index(drop=True)
    return snap


def build_runtime_snapshot_row(
    vehicles: pd.DataFrame,
    trips: pd.DataFrame,
    tx: pd.DataFrame,
    vehicle_id: int,
    snapshot_at: pd.Timestamp,
) -> pd.DataFrame:
    """One inference row at snapshot_at (no target columns)."""
    snapshot_at = pd.Timestamp(snapshot_at)
    v = vehicles[vehicles["vehicle_id"].astype(int) == int(vehicle_id)].copy()
    if v.empty:
        raise ValueError(f"vehicle_id={vehicle_id} not in vehicles frame")

    t = trips[trips["vehicle_id"].astype(int) == int(vehicle_id)].copy()
    x = tx[tx["vehicle_id"].astype(int) == int(vehicle_id)].copy()
    if not t.empty:
        t = t[t["entered_at"] <= snapshot_at]
    if not x.empty:
        x = x[x["occurred_at"] <= snapshot_at]

    reg = pd.to_datetime(v["registered_at"].iloc[0]).normalize()
    end_date = max(snapshot_at.normalize(), reg)
    if not t.empty:
        min_date = min(reg, t["entered_at"].min().normalize())
    else:
        min_date = reg

    t_all = t
    if t_all.empty:
        t_all = pd.DataFrame(
            columns=[
                "trip_id",
                "vehicle_id",
                "entered_at",
                "exited_at",
                "trip_amount",
                "is_paid",
                "payment_due_at",
            ]
        )
    else:
        t_all = t_all.copy()

    if x.empty:
        x = pd.DataFrame(
            columns=[
                "transaction_id",
                "vehicle_id",
                "occurred_at",
                "operation_type",
                "direction",
                "amount",
                "balance_after",
            ]
        )

    # Extend grid to snapshot date
    pad_dates = pd.date_range(min_date, end_date, freq="D")
    df = _build_daily_feature_grid(v, t_all, x, include_targets=False)
    if df.empty:
        row = {c: 0 for c in numeric_feature_cols(pd.DataFrame())}
        row["vehicle_id"] = vehicle_id
        row["snapshot_at"] = snapshot_at
        row["estimated_segment_from_history"] = "new_user"
        return pd.DataFrame([row])

    sub = df[df["vehicle_id"].astype(int) == int(vehicle_id)]
    sub = sub[sub["date"] <= end_date]
    if sub.empty:
        sub = df[df["vehicle_id"].astype(int) == int(vehicle_id)].tail(1)
    else:
        sub = sub.tail(1)

    sub = sub.copy()
    sub["snapshot_at"] = snapshot_at
    sub["estimated_segment_from_history"] = sub.apply(
        lambda r: infer_segment_from_snapshot(r.to_dict()), axis=1
    )
    feature_cols = inference_feature_columns(sub)
    out = sub[feature_cols + ["vehicle_id", "snapshot_at"]].copy()
    return out


def numeric_feature_cols(df: pd.DataFrame) -> List[str]:
    exclude = {"vehicle_id", "snapshot_at"} | TARGET_COLUMNS
    cats = set(CATEGORICAL_FEATURES)
    return [
        c
        for c in df.columns
        if c not in exclude | cats and pd.api.types.is_numeric_dtype(df[c])
    ]


def inference_feature_columns(df: pd.DataFrame) -> List[str]:
    return numeric_feature_cols(df) + CATEGORICAL_FEATURES


def feature_column_manifest(snap: pd.DataFrame) -> dict:
    num = numeric_feature_cols(snap)
    cat = list(CATEGORICAL_FEATURES)
    return {
        "numeric_features": num,
        "categorical_features": cat,
        "all_model_features": num + cat,
        "target_columns": sorted(TARGET_COLUMNS),
        "excluded_from_inference": sorted(TARGET_COLUMNS),
    }
