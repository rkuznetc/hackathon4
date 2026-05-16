#!/usr/bin/env python3
"""Offline ML training pipeline (not part of FastAPI runtime)."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingClassifier, RandomForestRegressor
from sklearn.metrics import (
    adjusted_rand_score,
    average_precision_score,
    calinski_harabasz_score,
    davies_bouldin_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
    silhouette_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.ml.snapshot_features import (  # noqa: E402
    build_snapshot_features,
    feature_column_manifest,
    numeric_feature_cols,
)

SEGMENT_SELECTED_FEATURES = [
    "w30_trips",
    "w30_spend",
    "w30_avg_amount",
    "w30_avg_duration",
    "w30_active_days",
    "w30_weekend_share",
    "w30_morning_share",
    "w30_short_share",
    "w30_long_share",
    "w30_zero_share",
    "w30_trips_per_active_day",
    "total_trips_to_date",
    "days_since_registration",
    "balance_at_snapshot",
    "tx30_topup_count",
    "tx30_fine_count",
    "tx30_negative_balance_events",
    "days_since_last_trip",
]


def rmse(y_true, y_pred) -> float:
    return math.sqrt(mean_squared_error(y_true, y_pred))


def mape_safe(y_true, y_pred) -> float:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    mask = y_true > 1e-9
    if mask.sum() == 0:
        return float("nan")
    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])))


def time_split_holdout(
    df: pd.DataFrame,
    time_col: str,
    *,
    holdout_frac: float = 0.2,
    horizon_trim_days: int = 0,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    """
    Time-based train/test split from actual data range (not fixed calendar dates).
    Avoids empty test set when snapshots end before hard-coded split like 2026-08-01.
    """
    work = df.copy()
    work[time_col] = pd.to_datetime(work[time_col])
    if horizon_trim_days > 0:
        end = work[time_col].max() - pd.Timedelta(days=horizon_trim_days)
        work = work[work[time_col] <= end]
    if work.empty:
        raise ValueError(f"Нет строк для разбиения по колонке {time_col}")

    ordered = work.sort_values(time_col).reset_index(drop=True)
    n = len(ordered)
    cut = max(1, min(int(n * (1 - holdout_frac)), n - 1))
    split_at = ordered[time_col].iloc[cut]
    train = ordered.iloc[:cut]
    test = ordered.iloc[cut:]
    if train.empty or test.empty:
        raise ValueError(
            f"Не удалось разбить {time_col}: строк={n}, "
            f"диапазон {ordered[time_col].min().date()} — {ordered[time_col].max().date()}"
        )
    return train, test, pd.Timestamp(split_at)


def regression_metrics_safe(y_true, y_pred) -> Dict[str, float]:
    if len(y_true) == 0:
        return {
            "mae": float("nan"),
            "rmse": float("nan"),
            "mape_nonzero": float("nan"),
            "r2": float("nan"),
        }
    return {
        "mae": mean_absolute_error(y_true, y_pred),
        "rmse": rmse(y_true, y_pred),
        "mape_nonzero": mape_safe(y_true, y_pred),
        "r2": r2_score(y_true, y_pred),
    }


def read_data(data_dir: Path) -> tuple[pd.DataFrame, ...]:
    required = [
        "vehicles.csv",
        "trips.csv",
        "account_transactions.csv",
        "recommendation_events.csv",
    ]
    missing = [n for n in required if not (data_dir / n).exists()]
    if missing:
        raise FileNotFoundError(
            f"В {data_dir} нет файлов: {missing}. Сначала запустите generator."
        )

    vehicles = pd.read_csv(
        data_dir / "vehicles.csv",
        parse_dates=["registered_at", "subscription_valid_until"],
    )
    trips = pd.read_csv(
        data_dir / "trips.csv",
        parse_dates=["entered_at", "exited_at", "payment_due_at"],
    )
    tx = pd.read_csv(data_dir / "account_transactions.csv", parse_dates=["occurred_at"])
    recs = pd.read_csv(
        data_dir / "recommendation_events.csv",
        parse_dates=["shown_at", "responded_at"],
    )
    seeds_path = data_dir / "driver_seeds.json"
    if seeds_path.exists():
        seeds = pd.DataFrame(json.loads(seeds_path.read_text(encoding="utf-8")))
    else:
        seeds = pd.DataFrame()
    return vehicles, trips, tx, recs, seeds


def make_preprocessor(df: pd.DataFrame) -> Tuple[List[str], List[str], ColumnTransformer]:
    num = numeric_feature_cols(df)
    cat = ["estimated_segment_from_history"]
    preprocess = ColumnTransformer(
        [
            ("num", StandardScaler(), num),
            ("cat", OneHotEncoder(handle_unknown="ignore"), cat),
        ]
    )
    return num, cat, preprocess


def train_spend_model(
    snap: pd.DataFrame, target: str, horizon: int, random_state: int
) -> Tuple[pd.DataFrame, Pipeline]:
    pool = snap.copy()
    pool["snapshot_at"] = pd.to_datetime(pool["snapshot_at"])
    # Drop tail snapshots only when the calendar span allows it (full ~30d labels).
    if horizon == 30:
        max_at, min_at = pool["snapshot_at"].max(), pool["snapshot_at"].min()
        span_days = (max_at - min_at).days
        desired_trim = 23
        if span_days > desired_trim:
            trimmed = pool[pool["snapshot_at"] <= max_at - pd.Timedelta(days=desired_trim)]
            if len(trimmed) >= 10:
                pool = trimmed.copy()
            else:
                print(
                    f"  {target}: skip {desired_trim}d tail trim "
                    f"(span={span_days}d, rows after trim={len(trimmed)})"
                )
        else:
            print(
                f"  {target}: skip tail trim (snapshot span {span_days}d "
                f"<= {desired_trim}d); using partial 30d targets"
            )
    train, test, split_at = time_split_holdout(
        pool, "snapshot_at", holdout_frac=0.2, horizon_trim_days=0
    )
    print(
        f"  {target}: split {split_at.date()}, train={len(train)}, test={len(test)}"
    )
    num, cat, preprocess = make_preprocessor(pool)
    X_train, y_train = train[num + cat], train[target]
    X_test, y_test = test[num + cat], test[target]

    baseline_col = "w7_spend" if horizon == 7 else "w30_spend"
    baseline_pred = test[baseline_col].to_numpy(float)
    baseline = {
        "task": target,
        "method": f"baseline_last_{horizon}d_spend",
        "train_rows": len(train),
        "test_rows": len(test),
        **regression_metrics_safe(y_test, baseline_pred),
    }

    model = RandomForestRegressor(
        n_estimators=180, min_samples_leaf=5, n_jobs=1, random_state=random_state
    )
    pipe = Pipeline([("prep", preprocess), ("model", model)])
    pipe.fit(X_train, y_train)
    pred = np.clip(pipe.predict(X_test), 0, None)
    ml = {
        "task": target,
        "method": "RandomForestRegressor",
        "train_rows": len(train),
        "test_rows": len(test),
        **regression_metrics_safe(y_test, pred),
    }
    return pd.DataFrame([baseline, ml]), pipe


def classification_metrics(y_true, prob) -> Dict[str, float]:
    pred = (prob >= 0.5).astype(int)
    cutoff = np.quantile(prob, 0.8) if len(prob) else 1.0
    pred20 = (prob >= cutoff).astype(int)
    return {
        "roc_auc": roc_auc_score(y_true, prob) if len(np.unique(y_true)) == 2 else float("nan"),
        "pr_auc": average_precision_score(y_true, prob)
        if len(np.unique(y_true)) == 2
        else float("nan"),
        "precision_0_5": precision_score(y_true, pred, zero_division=0),
        "recall_0_5": recall_score(y_true, pred, zero_division=0),
        "f1_0_5": f1_score(y_true, pred, zero_division=0),
        "precision_top20pct": precision_score(y_true, pred20, zero_division=0),
        "recall_top20pct": recall_score(y_true, pred20, zero_division=0),
        "alert_rate_top20pct": float(pred20.mean()),
        "positive_rate": float(np.mean(y_true)),
    }


def train_debt_risk_model(
    snap: pd.DataFrame, random_state: int
) -> Tuple[pd.DataFrame, Pipeline]:
    train, test, split_at = time_split_holdout(snap, "snapshot_at", holdout_frac=0.2)
    print(f"  debt_next_7d: split {split_at.date()}, train={len(train)}, test={len(test)}")
    pool = snap.copy()
    num, cat, preprocess = make_preprocessor(pool)
    X_train, y_train = train[num + cat], train["debt_next_7d"]
    X_test, y_test = test[num + cat], test["debt_next_7d"]

    score = (
        (test["w7_spend"] - test["balance_at_snapshot"]).clip(lower=0)
        + 300 * test["tx30_fine_count"]
        + 200 * test["tx30_negative_balance_events"]
    ).to_numpy(float)
    baseline_prob = (
        (score - score.min()) / (score.max() - score.min())
        if score.max() > score.min()
        else np.zeros_like(score)
    )
    baseline = {
        "task": "debt_next_7d",
        "method": "baseline_balance_vs_recent_spend",
        "train_rows": len(train),
        "test_rows": len(test),
        **classification_metrics(y_test.to_numpy(), baseline_prob),
    }

    model = GradientBoostingClassifier(
        n_estimators=140,
        learning_rate=0.055,
        max_depth=3,
        random_state=random_state,
    )
    pipe = Pipeline([("prep", preprocess), ("model", model)])
    pipe.fit(X_train, y_train)
    prob = pipe.predict_proba(X_test)[:, 1]
    ml = {
        "task": "debt_next_7d",
        "method": "GradientBoostingClassifier",
        "train_rows": len(train),
        "test_rows": len(test),
        **classification_metrics(y_test.to_numpy(), prob),
    }
    return pd.DataFrame([baseline, ml]), pipe


def segment_drivers(
    snap: pd.DataFrame, seeds: pd.DataFrame, random_state: int
) -> Tuple[pd.DataFrame, KMeans, StandardScaler, List[str]]:
    last = snap.sort_values("snapshot_at").groupby("vehicle_id").tail(1)
    if not seeds.empty and "archetype" in seeds.columns:
        last = last.merge(seeds[["vehicle_id", "archetype"]], on="vehicle_id", how="left")
    else:
        last["archetype"] = "unknown"

    selected = SEGMENT_SELECTED_FEATURES
    X = last[selected].fillna(0).copy()
    for c in X.columns:
        if (X[c] >= 0).all() and "share" not in c:
            X[c] = np.log1p(X[c])
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    model = KMeans(n_clusters=5, n_init=20, random_state=random_state)
    labels = model.fit_predict(Xs)
    last["ml_cluster"] = labels

    metrics = {
        "method": "KMeans_k5_compact_snapshot",
        "k": 5,
        "vehicles": len(last),
        "silhouette": silhouette_score(Xs, labels),
        "davies_bouldin": davies_bouldin_score(Xs, labels),
        "calinski_harabasz": calinski_harabasz_score(Xs, labels),
    }
    if "archetype" in last.columns and last["archetype"].notna().any():
        metrics["ari_vs_hidden_profile"] = adjusted_rand_score(
            last["archetype"], labels
        )
    else:
        metrics["ari_vs_hidden_profile"] = None
        metrics["note"] = "driver_seeds.json missing; segmentation metrics without hidden archetype"

    return pd.DataFrame([metrics]), model, scaler, selected


def parse_ruble_value(title: str) -> float:
    if not isinstance(title, str):
        return 0.0
    nums = re.findall(r"(\d[\d\s]*)\s*₽", title)
    if not nums:
        return 0.0
    return float(nums[-1].replace(" ", ""))


def business_priority(rec_type: str) -> int:
    return {
        "repay_debt": 10,
        "topup_balance": 8,
        "topup_forecast": 7,
        "buy_subscription": 7,
        "enable_autopay": 6,
        "pay_before_deadline": 5,
    }.get(rec_type, 4)


def build_recommendation_features(
    recs: pd.DataFrame,
    snap: pd.DataFrame,
    spend7_model: Pipeline,
    spend30_model: Pipeline,
    debt_model: Pipeline,
) -> pd.DataFrame:
    resolved = recs[recs["status"].isin(["accepted", "dismissed", "expired"])].copy()
    resolved["shown_at"] = pd.to_datetime(resolved["shown_at"])
    resolved = resolved.sort_values(["shown_at", "vehicle_id"]).reset_index(drop=True)
    resolved["target_accepted"] = resolved["status"].eq("accepted").astype(int)
    resolved["business_priority"] = resolved["recommendation_type"].map(business_priority)
    resolved["estimated_value"] = resolved["title"].map(parse_ruble_value)

    snap2 = snap.sort_values(["snapshot_at", "vehicle_id"]).reset_index(drop=True)
    merged = pd.merge_asof(
        resolved.sort_values("shown_at"),
        snap2.sort_values("snapshot_at"),
        left_on="shown_at",
        right_on="snapshot_at",
        by="vehicle_id",
        direction="backward",
    )
    merged = merged.dropna(subset=["snapshot_at"]).copy()

    num, cat, _ = make_preprocessor(snap)
    X = merged[num + cat]
    merged["pred_spend_7d"] = np.clip(spend7_model.predict(X), 0, None)
    merged["pred_spend_30d"] = np.clip(spend30_model.predict(X), 0, None)
    merged["pred_debt_risk_7d"] = debt_model.predict_proba(X)[:, 1]

    missing = merged["estimated_value"].fillna(0).le(0)
    if missing.any():
        m = merged.loc[missing]
        estimate = []
        for _, row in m.iterrows():
            rec_type = row["recommendation_type"]
            if rec_type == "repay_debt":
                value = abs(min(0, row["balance_at_snapshot"])) + 150 * row["tx30_fine_count"] + 300
            elif rec_type in {"topup_balance", "topup_forecast"}:
                value = max(0, row["pred_spend_7d"] - row["balance_at_snapshot"])
            elif rec_type == "enable_autopay":
                value = 200 + 150 * row["tx30_fine_count"] + 100 * row["tx30_negative_balance_events"]
            elif rec_type == "pay_before_deadline":
                value = 150
            else:
                value = 0
            estimate.append(float(max(0, value)))
        merged.loc[missing, "estimated_value"] = estimate
    return merged


def smoothed_acceptance_by_type(train: pd.DataFrame, alpha: float = 5.0) -> Dict[str, float]:
    global_mean = float(train["target_accepted"].mean())
    stats = train.groupby("recommendation_type")["target_accepted"].agg(["sum", "count"])
    return {
        idx: float((row["sum"] + alpha * global_mean) / (row["count"] + alpha))
        for idx, row in stats.iterrows()
    }


def evaluate_acceptance_and_ranking(
    rec_feat: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, float]]:
    df = rec_feat.copy()
    df["shown_at"] = pd.to_datetime(df["shown_at"])
    if df.empty:
        empty = pd.DataFrame()
        return empty, empty, {}

    train, test, split_at = time_split_holdout(df, "shown_at", holdout_frac=0.2)
    print(
        f"  recommendations: split {split_at.date()}, train={len(train)}, test={len(test)}"
    )
    test = test.copy()
    priors = smoothed_acceptance_by_type(train)
    global_mean = float(train["target_accepted"].mean()) if len(train) else 0.0
    test["acceptance_probability"] = test["recommendation_type"].map(priors).fillna(global_mean)
    if len(test) > 0:
        acc_extra = classification_metrics(
            test["target_accepted"].to_numpy(),
            test["acceptance_probability"].to_numpy(),
        )
    else:
        acc_extra = {
            "roc_auc": float("nan"),
            "pr_auc": float("nan"),
            "precision_0_5": float("nan"),
            "recall_0_5": float("nan"),
            "f1_0_5": float("nan"),
            "precision_top20pct": float("nan"),
            "recall_top20pct": float("nan"),
            "alert_rate_top20pct": float("nan"),
            "positive_rate": float("nan"),
        }
    acc_metrics = pd.DataFrame(
        [
            {
                "task": "recommendation_acceptance",
                "method": "smoothed_acceptance_rate_by_recommendation_type",
                "train_rows": len(train),
                "test_rows": len(test),
                **acc_extra,
            }
        ]
    )

    if test.empty:
        return acc_metrics, pd.DataFrame(), priors

    val = test["estimated_value"].fillna(0)
    test["business_score"] = test["business_priority"].astype(float)
    val_norm = np.log1p(val) / np.log1p(max(1.0, val.max()))
    pr_norm = test["business_priority"] / 10.0
    test["final_score"] = (
        0.30 * test["acceptance_probability"]
        + 0.40 * val_norm
        + 0.20 * test["pred_debt_risk_7d"]
        + 0.10 * pr_norm
    )

    rows = []
    overall = float(test["target_accepted"].mean()) if len(test) else 0.0
    rows.append(
        {
            "strategy": "overall_test_average",
            "groups": test["vehicle_id"].nunique(),
            "acceptance_at_1": overall,
            "expected_value_at_1": float(test["estimated_value"].mean()) if len(test) else 0.0,
            "lift_vs_overall": 1.0,
        }
    )
    multi = test.groupby("vehicle_id").filter(lambda x: len(x) >= 2)

    def eval_strategy(name: str, score_col: str) -> None:
        if multi.empty:
            return
        top1 = multi.sort_values(["vehicle_id", score_col], ascending=[True, False]).groupby(
            "vehicle_id"
        ).head(1)
        top3 = multi.sort_values(["vehicle_id", score_col], ascending=[True, False]).groupby(
            "vehicle_id"
        ).head(3)
        rows.append(
            {
                "strategy": name,
                "groups": top1["vehicle_id"].nunique(),
                "acceptance_at_1": float(top1["target_accepted"].mean()),
                "acceptance_at_3_any": float(
                    top3.groupby("vehicle_id")["target_accepted"].max().mean()
                ),
                "expected_value_at_1": float(top1["estimated_value"].mean()),
                "debt_risk_at_1": float(top1["pred_debt_risk_7d"].mean()),
                "lift_vs_overall": float(top1["target_accepted"].mean() / overall)
                if overall
                else float("nan"),
            }
        )

    eval_strategy("business_priority_baseline", "business_score")
    eval_strategy("acceptance_probability_only", "acceptance_probability")
    eval_strategy("hybrid_final_score", "final_score")
    ranking = pd.DataFrame(rows)
    return acc_metrics, ranking, priors


def save_model_artifacts(
    out_dir: Path,
    *,
    spend7_model: Pipeline,
    spend30_model: Pipeline,
    debt_model: Pipeline,
    segment_model: KMeans,
    segment_scaler: StandardScaler,
    segment_features: List[str],
    feature_manifest: dict,
    recommendation_priors: Dict[str, float],
    metadata: dict,
) -> None:
    models_dir = out_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(spend7_model, models_dir / "spend_7d_model.joblib")
    joblib.dump(spend30_model, models_dir / "spend_30d_model.joblib")
    joblib.dump(debt_model, models_dir / "debt_risk_model.joblib")
    joblib.dump(segment_model, models_dir / "segment_model.joblib")
    joblib.dump(segment_scaler, models_dir / "segment_scaler.joblib")
    (models_dir / "segment_feature_columns.json").write_text(
        json.dumps(segment_features, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (models_dir / "feature_columns.json").write_text(
        json.dumps(feature_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (models_dir / "recommendation_priors.json").write_text(
        json.dumps(recommendation_priors, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (models_dir / "model_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def df_to_markdown_table(df: pd.DataFrame) -> str:
    """Markdown table without optional pandas/tabulate dependency."""
    rounded = df.round(4)
    cols = [str(c) for c in rounded.columns]
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = [
        "| " + " | ".join(str(row[c]) for c in rounded.columns) + " |"
        for _, row in rounded.iterrows()
    ]
    return "\n".join([header, sep, *body])


def write_report(
    out_dir: Path,
    snap: pd.DataFrame,
    spend7: pd.DataFrame,
    spend30: pd.DataFrame,
    debt: pd.DataFrame,
    seg: pd.DataFrame,
    acc: pd.DataFrame,
    ranking: pd.DataFrame,
) -> str:
    def row_method(df: pd.DataFrame, method: str) -> pd.Series:
        return df[df["method"].eq(method)].iloc[0]

    s7 = row_method(spend7, "RandomForestRegressor")
    s30 = row_method(spend30, "RandomForestRegressor")
    d = row_method(debt, "GradientBoostingClassifier")
    lines = [
        "# ML pipeline report",
        "",
        f"Snapshot rows: **{len(snap):,}**, vehicles: **{snap['vehicle_id'].nunique()}**.",
        "",
        "## Spend models",
        df_to_markdown_table(spend7),
        "",
        df_to_markdown_table(spend30),
        "",
        f"7d MAE: **{s7['mae']:.2f}**, 30d MAE: **{s30['mae']:.2f}**.",
        "",
        "## Debt risk",
        df_to_markdown_table(debt),
        "",
        "## Segmentation",
        df_to_markdown_table(seg),
        "",
        "## Recommendation acceptance",
        df_to_markdown_table(acc),
        "",
        "## Ranking",
        df_to_markdown_table(ranking),
    ]
    report = "\n".join(lines)
    (out_dir / "ml_final_report.md").write_text(report, encoding="utf-8")
    summary = {
        "snapshot_rows": int(len(snap)),
        "vehicles": int(snap["vehicle_id"].nunique()),
        "spend_7d": s7.to_dict(),
        "spend_30d": s30.to_dict(),
        "debt_risk": d.to_dict(),
        "segmentation": seg.iloc[0].to_dict(),
        "acceptance": acc.iloc[0].to_dict(),
        "ranking": ranking.to_dict(orient="records"),
    }
    (out_dir / "ml_final_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return report


def run_pipeline(
    data_dir: Path,
    out_dir: Path,
    *,
    random_state: int,
    save_models: bool,
    write_reports: bool,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    vehicles, trips, tx, recs, seeds = read_data(data_dir)

    print("Building snapshot features...")
    snap = build_snapshot_features(vehicles, trips, tx, include_targets=True)
    if write_reports:
        snap.to_csv(out_dir / "driver_snapshot_features.csv", index=False)
    print(f"Snapshot rows: {len(snap)}")

    print("Training spend models...")
    spend7_metrics, spend7_model = train_spend_model(
        snap, "spend_next_7d", 7, random_state
    )
    spend30_metrics, spend30_model = train_spend_model(
        snap, "spend_next_30d", 30, random_state
    )
    if write_reports:
        spend7_metrics.to_csv(out_dir / "spend_7d_metrics.csv", index=False)
        spend30_metrics.to_csv(out_dir / "spend_30d_metrics.csv", index=False)

    print("Training debt risk model...")
    debt_metrics, debt_model = train_debt_risk_model(snap, random_state)
    if write_reports:
        debt_metrics.to_csv(out_dir / "debt_risk_metrics.csv", index=False)

    print("Segmentation KMeans...")
    seg_metrics, segment_model, segment_scaler, segment_features = segment_drivers(
        snap, seeds, random_state
    )
    if write_reports:
        seg_metrics.to_csv(out_dir / "segmentation_metrics.csv", index=False)

    print("Recommendation features and ranking...")
    rec_feat = build_recommendation_features(
        recs, snap, spend7_model, spend30_model, debt_model
    )
    if write_reports:
        rec_feat.to_csv(out_dir / "recommendation_event_features.csv", index=False)
    acc_metrics, ranking, priors = evaluate_acceptance_and_ranking(rec_feat)
    if write_reports:
        acc_metrics.to_csv(out_dir / "acceptance_metrics.csv", index=False)
        ranking.to_csv(out_dir / "recommendation_ranking_metrics.csv", index=False)
        write_report(out_dir, snap, spend7_metrics, spend30_metrics, debt_metrics, seg_metrics, acc_metrics, ranking)

    manifest = feature_column_manifest(snap)
    s7_row = spend7_metrics[spend7_metrics["method"] == "RandomForestRegressor"].iloc[0]
    s30_row = spend30_metrics[spend30_metrics["method"] == "RandomForestRegressor"].iloc[0]
    d_row = debt_metrics[debt_metrics["method"] == "GradientBoostingClassifier"].iloc[0]

    import sklearn

    metadata = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(data_dir.resolve()),
        "out_dir": str(out_dir.resolve()),
        "random_state": random_state,
        "vehicles": int(vehicles["vehicle_id"].nunique()),
        "snapshot_rows": int(len(snap)),
        "sklearn_version": sklearn.__version__,
        "pandas_version": pd.__version__,
        "numpy_version": np.__version__,
        "python_version": sys.version.split()[0],
        "saved_models": [],
        "metrics": {
            "spend_7d": {
                "mae": float(s7_row["mae"]),
                "rmse": float(s7_row["rmse"]),
                "r2": float(s7_row["r2"]),
            },
            "spend_30d": {
                "mae": float(s30_row["mae"]),
                "rmse": float(s30_row["rmse"]),
                "r2": float(s30_row["r2"]),
            },
            "debt_risk": {
                "roc_auc": float(d_row["roc_auc"]),
                "pr_auc": float(d_row["pr_auc"]),
                "f1_0_5": float(d_row["f1_0_5"]),
            },
            "segmentation": seg_metrics.iloc[0].to_dict(),
            "acceptance": acc_metrics.iloc[0].to_dict(),
        },
        "runtime_models": [
            "spend_7d_model",
            "spend_30d_model",
            "debt_risk_model",
        ],
        "segmentation_runtime": False,
    }

    if save_models:
        save_model_artifacts(
            out_dir,
            spend7_model=spend7_model,
            spend30_model=spend30_model,
            debt_model=debt_model,
            segment_model=segment_model,
            segment_scaler=segment_scaler,
            segment_features=segment_features,
            feature_manifest=manifest,
            recommendation_priors=priors,
            metadata=metadata,
        )
        metadata["saved_models"] = [
            "spend_7d_model.joblib",
            "spend_30d_model.joblib",
            "debt_risk_model.joblib",
            "segment_model.joblib",
            "segment_scaler.joblib",
            "feature_columns.json",
            "recommendation_priors.json",
            "model_metadata.json",
        ]
        print(f"Models saved to {out_dir / 'models'}")

    return metadata


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Offline ML training for toll-roads dataset.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: <data-dir>/ml_final)",
    )
    parser.add_argument("--save-models", action="store_true")
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument(
        "--no-reports",
        action="store_true",
        help="Skip CSV/MD reports (only models + metadata if --save-models)",
    )
    args = parser.parse_args(argv)

    data_dir = args.data_dir
    if not data_dir.is_absolute():
        data_dir = (Path.cwd() / data_dir).resolve()
    out_dir = args.out_dir
    if out_dir is None:
        out_dir = data_dir / "ml_final"
    elif not out_dir.is_absolute():
        out_dir = (Path.cwd() / out_dir).resolve()

    meta = run_pipeline(
        data_dir,
        out_dir,
        random_state=args.random_state,
        save_models=args.save_models,
        write_reports=not args.no_reports,
    )
    print(json.dumps(meta, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
