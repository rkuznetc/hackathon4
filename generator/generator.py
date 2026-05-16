from __future__ import annotations

import csv
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

DATA_END_DATE = date(2026, 7, 31)
HISTORY_DAYS = 40
NEW_USER_HISTORY_DAYS = 14
RANDOM_SEED = 42
LOCAL_TZ = ZoneInfo("Europe/Moscow")
OUTPUT_DIR = Path(__file__).resolve().parent / "data"

# Основной ML-датасет: 100 водителей.
# Распределение намеренно не идеально равное, чтобы выборка была ближе к реальной:
# больше регулярных пользователей, меньше новичков.
ARCHETYPE_COUNTS = {
    "commuter": 28,
    "taxi_driver": 22,
    "weekend_guest": 20,
    "tourist": 18,
    "new_user": 12,
}

SEGMENT_NAMES = {
    "commuter": "Работающий",
    "weekend_guest": "Гость выходного дня",
    "taxi_driver": "Таксист",
    "tourist": "Турист",
    "new_user": "Новичок",
}

VEHICLE_FIELDS = [
    "vehicle_id", "license_plate", "owner_name", "registered_at", "phone",
    "current_balance", "autopay_enabled", "has_subscription",
    "subscription_type", "subscription_valid_until", "account_status",
]
TRIP_FIELDS = ["trip_id", "vehicle_id", "entered_at", "exited_at", "trip_amount", "is_paid", "payment_due_at"]
TX_FIELDS = [
    "transaction_id", "vehicle_id", "occurred_at", "operation_type",
    "direction", "amount", "balance_after", "trip_id", "recommendation_event_id",
]
FEATURE_FIELDS = [
    "vehicle_id", "updated_at", "trips_7d", "trips_30d", "avg_trip_amount", "avg_trip_duration_min",
    "weekend_trip_share", "morning_entry_share", "topup_count_30d", "avg_topup_amount",
    "debt_episodes_30d", "fines_count_30d", "days_since_registration", "trip_count_total",
    "segment_code", "segment_name", "segment_assigned_at",
]
RECO_FIELDS = [
    "event_id", "vehicle_id", "shown_at", "recommendation_type", "title",
    "status", "responded_at", "deep_link", "related_transaction_id",
]

FINE_AMOUNT = 150.0
FINE_DELAY_HOURS_AFTER_DUE = 24

# Тарифная модель платного пребывания на территории.
# Таблицы БД не меняются: итоговая цена сохраняется в trips.trip_amount.
BASE_RATE_PER_HOUR = 18.0
ENTRY_FEE = 55.0
FREE_MINUTES = 15
DAILY_CAP_HOURS = 10

SEASON_COEFFICIENTS = {
    "low": 0.7,
    "medium": 1.5,
    "high": 2.0,
}
DAY_COEFFICIENTS = {
    "workday": 1.0,
    "weekend": 1.3,
    "event": 2.0,
}
HOUR_COEFFICIENTS = {
    "night": 0.5,
    "morning": 1.8,
    "day": 1.0,
    "evening": 2.0,
    "chill": 1.2,
}
EVENT_DAYS = {
    date(2026, 7, 4),
    date(2026, 7, 18),
    date(2026, 7, 25),
}


def is_event_day(day: date) -> bool:
    return day in EVENT_DAYS


def ensure_local_dt(dt: datetime) -> datetime:
    """Тариф считаем в локальном времени. В CSV по-прежнему сохраняем naive datetime."""
    return dt.replace(tzinfo=LOCAL_TZ) if dt.tzinfo is None else dt.astimezone(LOCAL_TZ)
SUBSCRIPTION_PRICES = {
    "daily_unlimited": 490.0,
    "weekend_pack": 1490.0,
    "trip_pack_10": 990.0,
    "trip_pack_30": 2490.0,
    "monthly_unlimited": 3490.0,
}
PACK_TRIP_LIMITS = {
    "trip_pack_10": 10,
    "trip_pack_30": 30,
}

@dataclass
class DriverSeed:
    vehicle_id: int
    archetype: str
    topup_style: str
    risk_tolerance: float
    app_engagement: float
    typical_trip_amount: float
    initial_balance: float
    grace_hours: int
    registered_at: str


def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def money(x: float) -> float:
    return round(float(x) + 1e-9, 2)


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def weighted_choice(rng: random.Random, items: list[tuple[str, float]]) -> str:
    total = sum(w for _, w in items)
    x = rng.random() * total
    acc = 0.0
    for item, w in items:
        acc += w
        if x <= acc:
            return item
    return items[-1][0]


def normal_minutes(rng: random.Random, mean: int, sd: int, lo: int, hi: int) -> int:
    return int(round(clamp(rng.gauss(mean, sd), lo, hi)))


def lognormal_amount(rng: random.Random, typical: float, sigma: float = 0.12, lo_factor: float = 0.65, hi_factor: float = 1.6) -> float:
    # Больше значений около typical, редкие отклонения вверх/вниз.
    val = rng.lognormvariate(math.log(typical), sigma)
    return money(clamp(val, typical * lo_factor, typical * hi_factor))


def date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def make_dt(day: date, minutes_from_midnight: int, rng: random.Random) -> datetime:
    minutes_from_midnight = int(clamp(minutes_from_midnight, 0, 23 * 60 + 59))
    return datetime.combine(day, time(minutes_from_midnight // 60, minutes_from_midnight % 60, rng.randint(0, 59)))


def poisson_like(rng: random.Random, lam: float) -> int:
    # Knuth Poisson, достаточно для малых/средних lambda.
    l = math.exp(-lam)
    k = 0
    p = 1.0
    while p > l:
        k += 1
        p *= rng.random()
    return k - 1


FIRST_NAMES = ["Алексей", "Мария", "Иван", "Елена", "Дмитрий", "Ольга", "Сергей", "Анна", "Павел", "Ирина", "Никита", "Дарья", "Кирилл", "Софья", "Максим"]
LAST_NAMES = ["Иванов", "Смирнов", "Кузнецов", "Попов", "Соколов", "Лебедев", "Козлов", "Новиков", "Морозов", "Петров", "Волков", "Соловьев"]
MOBILE_PREFIXES = ["901", "903", "905", "906", "909", "910", "915", "916", "925", "926", "929", "977", "985", "999"]


def unique_phone(rng: random.Random, used: set[str]) -> str:
    while True:
        phone = "+7" + rng.choice(MOBILE_PREFIXES) + f"{rng.randint(0, 9999999):07d}"
        if phone not in used:
            used.add(phone)
            return phone


def unique_plate(rng: random.Random, used: set[str]) -> str:
    letters = "АВЕКМНОРСТУХ"
    regions = ["23", "77", "93", "97", "99", "123", "150", "197", "199", "777", "797"]
    while True:
        plate = f"{rng.choice(letters)}{rng.randint(1, 999):03d}{rng.choice(letters)}{rng.choice(letters)}{rng.choice(regions)}"
        if plate not in used:
            used.add(plate)
            return plate


def make_owner_name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def build_seeds(rng: random.Random) -> list[DriverSeed]:
    raw = []
    for archetype, count in ARCHETYPE_COUNTS.items():
        for _ in range(count):
            if archetype == "commuter":
                typical = rng.uniform(85, 125); grace = 72
                style = weighted_choice(rng, [("proactive", .48), ("reactive", .42), ("negligent", .10)])
                reg = DATA_END_DATE - timedelta(days=rng.randint(70, 220))
                bal = rng.uniform(450, 2400)
            elif archetype == "weekend_guest":
                typical = rng.uniform(75, 135); grace = 48
                style = weighted_choice(rng, [("proactive", .35), ("reactive", .47), ("negligent", .18)])
                reg = DATA_END_DATE - timedelta(days=rng.randint(45, 180))
                bal = rng.uniform(250, 1600)
            elif archetype == "taxi_driver":
                typical = rng.uniform(75, 115); grace = 24
                style = weighted_choice(rng, [("proactive", .28), ("reactive", .52), ("negligent", .20)])
                reg = DATA_END_DATE - timedelta(days=rng.randint(80, 260))
                bal = rng.uniform(800, 3200)
            elif archetype == "tourist":
                typical = rng.uniform(95, 155); grace = 72
                style = weighted_choice(rng, [("proactive", .38), ("reactive", .45), ("negligent", .17)])
                reg = DATA_END_DATE - timedelta(days=rng.randint(35, 200))
                bal = rng.uniform(150, 1400)
            else:
                typical = rng.uniform(80, 125); grace = 48
                style = weighted_choice(rng, [("proactive", .12), ("reactive", .36), ("negligent", .52)])
                reg = DATA_END_DATE - timedelta(days=rng.randint(2, NEW_USER_HISTORY_DAYS))
                bal = rng.uniform(80, 500)

            if style == "negligent":
                bal = min(bal, rng.uniform(40, 420))
            raw.append((archetype, style, typical, grace, reg, bal))

    rng.shuffle(raw)
    seeds = []
    for vehicle_id, (archetype, style, typical, grace, reg, bal) in enumerate(raw, start=1):
        seeds.append(DriverSeed(
            vehicle_id=vehicle_id,
            archetype=archetype,
            topup_style=style,
            risk_tolerance=round(rng.uniform(0.2, 0.85), 3),
            app_engagement=round(rng.betavariate(2.2, 2.0), 3),
            typical_trip_amount=money(typical),
            initial_balance=money(bal),
            grace_hours=grace,
            registered_at=reg.isoformat(),
        ))
    return seeds


def make_vehicles(rng: random.Random, seeds: list[DriverSeed]) -> list[dict]:
    used_phones, used_plates = set(), set()
    rows = []
    for seed in seeds:
        has_sub = False
        sub_type = ""
        sub_until = ""
        if seed.archetype == "commuter" and rng.random() < .28:
            has_sub, sub_type = True, rng.choice(["monthly_unlimited", "trip_pack_30"])
        elif seed.archetype == "weekend_guest" and rng.random() < .22:
            has_sub, sub_type = True, rng.choice(["weekend_pack", "trip_pack_10"])
        elif seed.archetype == "taxi_driver" and rng.random() < .35:
            has_sub, sub_type = True, rng.choice(["monthly_unlimited", "trip_pack_30"])
        elif seed.archetype == "tourist" and rng.random() < .08:
            has_sub, sub_type = True, "daily_unlimited"
        if has_sub:
            sub_until = (DATA_END_DATE + timedelta(days=rng.randint(5, 55))).isoformat()

        if seed.archetype == "taxi_driver":
            autopay_p = .52
        elif seed.archetype == "commuter":
            autopay_p = .36
        elif seed.archetype == "weekend_guest":
            autopay_p = .16
        elif seed.archetype == "tourist":
            autopay_p = .10
        else:
            autopay_p = .04

        rows.append({
            "vehicle_id": seed.vehicle_id,
            "license_plate": unique_plate(rng, used_plates),
            "owner_name": make_owner_name(rng),
            "registered_at": seed.registered_at,
            "phone": unique_phone(rng, used_phones),
            "current_balance": seed.initial_balance,
            "autopay_enabled": int(rng.random() < autopay_p),
            "has_subscription": int(has_sub),
            "subscription_type": sub_type,
            "subscription_valid_until": sub_until,
            "account_status": "active",
        })
    return rows


def get_season_key(dt: datetime) -> str:
    dt = ensure_local_dt(dt)
    month = dt.month
    day = dt.day
    if (month == 6 and day >= 15) or month in (7, 8):
        return "high"
    if (month == 12 and day >= 28) or (month == 1 and day <= 10):
        return "high"
    if month == 5 or (month == 6 and day < 15) or month == 9:
        return "medium"
    return "low"


def get_day_type_key(dt: datetime) -> str:
    dt = ensure_local_dt(dt)
    if dt.date() in EVENT_DAYS:
        return "event"
    return "weekend" if dt.weekday() >= 5 else "workday"


def get_time_slot_key(hour: int) -> str:
    if hour >= 23 or hour < 7:
        return "night"
    if 7 <= hour < 10:
        return "morning"
    if 10 <= hour < 16:
        return "day"
    if 16 <= hour < 20:
        return "evening"
    return "chill"


def tariff_coefficient(dt: datetime) -> float:
    dt = ensure_local_dt(dt)
    return (
        SEASON_COEFFICIENTS[get_season_key(dt)]
        * DAY_COEFFICIENTS[get_day_type_key(dt)]
        * HOUR_COEFFICIENTS[get_time_slot_key(dt.hour)]
    )


def calculate_tariff_amount(entered: datetime, exited: datetime) -> float:
    """Стоимость пребывания по 15-минутным интервалам, с бесплатными 15 минутами и суточным лимитом."""
    entered_local = ensure_local_dt(entered)
    exited_local = ensure_local_dt(exited)
    total_minutes = int((exited_local - entered_local).total_seconds() // 60)
    if total_minutes < FREE_MINUTES:
        return 0.0

    subtotal = ENTRY_FEE
    current = entered_local
    interval = timedelta(minutes=15)
    while current < exited_local:
        rate_per_15m = BASE_RATE_PER_HOUR * tariff_coefficient(current) / 4
        subtotal += rate_per_15m
        current += interval

    max_hour_coef = max(HOUR_COEFFICIENTS.values())
    max_season_coef = max(SEASON_COEFFICIENTS.values())
    max_day_coef = max(DAY_COEFFICIENTS.values())
    daily_cap = (BASE_RATE_PER_HOUR * max_hour_coef * max_season_coef * max_day_coef) * DAILY_CAP_HOURS
    total_days = (exited_local.date() - entered_local.date()).days + 1
    return money(min(subtotal, daily_cap * total_days))


def subscription_covers_trip(vehicle: dict, entered: datetime, pack_remaining: dict[int, int]) -> bool:
    if int(vehicle.get("has_subscription") or 0) != 1:
        return False
    sub_type = vehicle.get("subscription_type") or ""
    valid_until = vehicle.get("subscription_valid_until") or ""
    if valid_until and entered.date() > date.fromisoformat(valid_until):
        return False

    if sub_type == "monthly_unlimited":
        return True
    if sub_type == "weekend_pack" and entered.weekday() >= 5:
        return True
    if sub_type == "daily_unlimited":
        # В рамках генератора считаем, что суточный безлимит покрывает дату визита,
        # если он активен. Отдельное поле даты действия в БД не требуется.
        return True
    if sub_type in PACK_TRIP_LIMITS:
        vid = int(vehicle["vehicle_id"])
        if pack_remaining.get(vid, 0) > 0:
            pack_remaining[vid] -= 1
            return True
    return False


def calculate_trip_amount_for_vehicle(entered: datetime, exited: datetime, vehicle: dict, pack_remaining: dict[int, int]) -> float:
    if subscription_covers_trip(vehicle, entered, pack_remaining):
        return 0.0
    return calculate_tariff_amount(entered, exited)


def trip_record(vehicle_id: int, entered: datetime, exited: datetime, amount: float, grace_hours: int) -> dict:
    return {
        "vehicle_id": vehicle_id,
        "entered_at": entered,
        "exited_at": exited,
        "trip_amount": amount,
        "is_paid": 0,
        "payment_due_at": exited + timedelta(hours=grace_hours),
    }


def generate_commuter_trips(rng: random.Random, seed: DriverSeed, start: date, end: date) -> list[dict]:
    rows = []
    weekday_p = rng.uniform(.72, .92)
    weekend_p = rng.uniform(.03, .16)
    personal_shift = rng.randint(-20, 25)
    for day in date_range(start, end):
        is_weekend = day.weekday() >= 5
        active_p = weekend_p if is_weekend else weekday_p
        if is_event_day(day):
            active_p = max(active_p, .24)
        if rng.random() > active_p:
            continue
        if is_weekend:
            if is_event_day(day):
                entry_min = normal_minutes(rng, 17 * 60 + 30, 110, 11 * 60, 22 * 60)
                duration = normal_minutes(rng, 210, 85, 70, 480)
            else:
                entry_min = normal_minutes(rng, 11 * 60 + 30, 110, 8 * 60, 18 * 60)
                duration = normal_minutes(rng, 160, 80, 45, 420)
        else:
            entry_min = normal_minutes(rng, 8 * 60 + 20 + personal_shift, 32, 7 * 60, 10 * 60)
            duration = normal_minutes(rng, 9 * 60 + 15, 75, 6 * 60, 13 * 60)
        entered = make_dt(day, entry_min, rng)
        exited = entered + timedelta(minutes=duration)
        rows.append(trip_record(seed.vehicle_id, entered, exited, lognormal_amount(rng, seed.typical_trip_amount, .10), seed.grace_hours))
    return rows


def generate_weekend_guest_trips(rng: random.Random, seed: DriverSeed, start: date, end: date) -> list[dict]:
    rows = []
    weekend_p = rng.uniform(.52, .86)
    weekday_noise_p = rng.uniform(.01, .05)
    for day in date_range(start, end):
        event = is_event_day(day)
        if day.weekday() >= 5:
            if rng.random() > (max(weekend_p, .92) if event else weekend_p):
                continue
            n = 1 + int(rng.random() < (.55 if event else .28)) + int(event and rng.random() < .18)
        else:
            if rng.random() > (.18 if event else weekday_noise_p):
                continue
            n = 1
        for _ in range(n):
            if event:
                entry_min = normal_minutes(rng, 17 * 60 + 20, 150, 10 * 60, 22 * 60)
                duration = normal_minutes(rng, 190, 80, 55, 430)
            else:
                entry_min = normal_minutes(rng, 13 * 60, 180, 9 * 60, 21 * 60)
                duration = normal_minutes(rng, 150, 70, 45, 360)
            entered = make_dt(day, entry_min, rng)
            exited = entered + timedelta(minutes=duration)
            rows.append(trip_record(seed.vehicle_id, entered, exited, lognormal_amount(rng, seed.typical_trip_amount, .14), seed.grace_hours))
    return rows


def generate_taxi_trips(rng: random.Random, seed: DriverSeed, start: date, end: date) -> list[dict]:
    rows = []
    active_p_weekday = rng.uniform(.76, .96)
    active_p_weekend = rng.uniform(.62, .88)
    intensity = rng.uniform(6.5, 11.5)
    for day in date_range(start, end):
        event = is_event_day(day)
        active_p = active_p_weekend if day.weekday() >= 5 else active_p_weekday
        if event:
            active_p = max(active_p, .98)
        if rng.random() > active_p:
            continue
        event_extra = rng.randint(3, 7) if event else 0
        n = max(3, min(22, poisson_like(rng, intensity) + rng.randint(-1, 2) + event_extra))
        current_min = normal_minutes(rng, 12 * 60 + 30, 120, 7 * 60, 16 * 60) if event else normal_minutes(rng, 8 * 60 + 30, 80, 6 * 60, 12 * 60)
        for _ in range(n):
            if current_min > 23 * 60:
                break
            entered = make_dt(day, current_min, rng)
            duration = normal_minutes(rng, 16, 7, 5, 35)
            exited = entered + timedelta(minutes=duration)
            rows.append(trip_record(seed.vehicle_id, entered, exited, lognormal_amount(rng, seed.typical_trip_amount, .08), seed.grace_hours))
            # Между короткими заездами бывает пауза; не равномерная, а экспоненциальная.
            current_min += duration + int(clamp(rng.expovariate(1 / 38), 8, 130))
    return rows


def generate_tourist_trips(rng: random.Random, seed: DriverSeed, start: date, end: date) -> list[dict]:
    rows = []
    total_days = (end - start).days + 1
    burst_count = 1 + int(rng.random() < .42)
    used_days = set()
    for _ in range(burst_count):
        visit_len = rng.randint(2, 5)
        visit_start = start + timedelta(days=rng.randint(0, max(0, total_days - visit_len)))
        for offset in range(visit_len):
            day = visit_start + timedelta(days=offset)
            if day > end or day in used_days:
                continue
            used_days.add(day)
            n = 1 + int(rng.random() < .38)
            for _ in range(n):
                entry_min = normal_minutes(rng, 12 * 60 + 30, 165, 8 * 60, 21 * 60)
                duration = normal_minutes(rng, 230, 110, 70, 520)
                entered = make_dt(day, entry_min, rng)
                exited = entered + timedelta(minutes=duration)
                rows.append(trip_record(seed.vehicle_id, entered, exited, lognormal_amount(rng, seed.typical_trip_amount, .16), seed.grace_hours))
    for day in sorted(d for d in EVENT_DAYS if start <= d <= end):
        if rng.random() < .48:
            n = 1 + int(rng.random() < .45)
            for _ in range(n):
                entry_min = normal_minutes(rng, 16 * 60 + 30, 180, 9 * 60, 22 * 60)
                duration = normal_minutes(rng, 260, 110, 90, 560)
                entered = make_dt(day, entry_min, rng)
                exited = entered + timedelta(minutes=duration)
                rows.append(trip_record(seed.vehicle_id, entered, exited, lognormal_amount(rng, seed.typical_trip_amount, .16), seed.grace_hours))
    return rows


def generate_new_user_trips(rng: random.Random, seed: DriverSeed, start: date, end: date) -> list[dict]:
    rows = []
    days = list(date_range(start, end))
    rng.shuffle(days)
    n = rng.randint(1, 4)
    for day in sorted(days[:n]):
        entry_min = normal_minutes(rng, 14 * 60, 250, 7 * 60, 22 * 60)
        duration = normal_minutes(rng, 90, 55, 20, 240)
        entered = make_dt(day, entry_min, rng)
        exited = entered + timedelta(minutes=duration)
        rows.append(trip_record(seed.vehicle_id, entered, exited, lognormal_amount(rng, seed.typical_trip_amount, .14), seed.grace_hours))
    return rows


def resolve_vehicle_trip_overlaps(rows: list[dict]) -> list[dict]:
    """Гарантирует, что у одного автомобиля поездки не пересекаются по времени."""
    by_vehicle: dict[int, list[dict]] = {}
    for row in rows:
        by_vehicle.setdefault(int(row["vehicle_id"]), []).append(row)

    fixed: list[dict] = []
    for vehicle_rows in by_vehicle.values():
        vehicle_rows.sort(key=lambda r: r["entered_at"])
        last_exit: Optional[datetime] = None
        for row in vehicle_rows:
            duration = row["exited_at"] - row["entered_at"]
            grace_delta = row["payment_due_at"] - row["exited_at"]
            if last_exit is not None and row["entered_at"] <= last_exit:
                row["entered_at"] = last_exit + timedelta(minutes=3)
                row["exited_at"] = row["entered_at"] + duration
                row["payment_due_at"] = row["exited_at"] + grace_delta
            last_exit = row["exited_at"]
            fixed.append(row)
    return fixed


def make_trips(rng: random.Random, seeds: list[DriverSeed], vehicles: list[dict]) -> list[dict]:
    rows = []
    generators = {
        "commuter": generate_commuter_trips,
        "weekend_guest": generate_weekend_guest_trips,
        "taxi_driver": generate_taxi_trips,
        "tourist": generate_tourist_trips,
        "new_user": generate_new_user_trips,
    }
    for seed in seeds:
        reg = date.fromisoformat(seed.registered_at)
        start = max(reg, DATA_END_DATE - timedelta(days=(NEW_USER_HISTORY_DAYS if seed.archetype == "new_user" else HISTORY_DAYS) - 1))
        rows.extend(generators[seed.archetype](rng, seed, start, DATA_END_DATE))
    rows = resolve_vehicle_trip_overlaps(rows)
    rows.sort(key=lambda r: (r["entered_at"], r["vehicle_id"]))

    vehicles_by_id = {int(v["vehicle_id"]): v for v in vehicles}
    pack_remaining = {
        int(v["vehicle_id"]): PACK_TRIP_LIMITS.get(v.get("subscription_type", ""), 0)
        for v in vehicles
    }
    for row in rows:
        vehicle = vehicles_by_id[int(row["vehicle_id"])]
        row["trip_amount"] = calculate_trip_amount_for_vehicle(
            row["entered_at"], row["exited_at"], vehicle, pack_remaining
        )

    for i, row in enumerate(rows, start=1):
        row["trip_id"] = i
        row["entered_at"] = fmt_dt(row["entered_at"])
        row["exited_at"] = fmt_dt(row["exited_at"])
        row["payment_due_at"] = fmt_dt(row["payment_due_at"])
    return rows


def add_tx(txs: list[dict], vehicle_id: int, at: datetime, operation_type: str, direction: str, amount: float, balance_after: float, trip_id: Optional[int] = None, recommendation_event_id: Optional[int] = None) -> dict:
    tx = {
        "transaction_id": None,
        "vehicle_id": vehicle_id,
        "occurred_at": at,
        "operation_type": operation_type,
        "direction": direction,
        "amount": money(amount),
        "balance_after": money(balance_after),
        "trip_id": trip_id or "",
        "recommendation_event_id": recommendation_event_id or "",
    }
    txs.append(tx)
    return tx


def topup_amount(rng: random.Random, style: str, monthly_need_hint: float = 900.0) -> float:
    if style == "proactive":
        return money(clamp(rng.lognormvariate(math.log(max(650, monthly_need_hint * .5)), .28), 650, 2600))
    if style == "reactive":
        return money(clamp(rng.lognormvariate(math.log(650), .32), 350, 1600))
    return money(clamp(rng.lognormvariate(math.log(350), .35), 180, 850))


def simulate_transactions(rng: random.Random, vehicles: list[dict], trips: list[dict], seeds: list[DriverSeed]) -> tuple[list[dict], list[dict], list[dict]]:
    seeds_by_id = {s.vehicle_id: s for s in seeds}
    trips_by_v: dict[int, list[dict]] = {}
    for trip in trips:
        trips_by_v.setdefault(int(trip["vehicle_id"]), []).append(trip)

    all_txs: list[dict] = []
    balances: dict[int, float] = {}
    for v in vehicles:
        vid = int(v["vehicle_id"])
        seed = seeds_by_id[vid]
        balance = float(v["current_balance"])
        balances[vid] = balance
        autopay = int(v["autopay_enabled"]) == 1
        pending = []
        v_trips = sorted(trips_by_v.get(vid, []), key=lambda t: t["exited_at"])
        monthly_hint = sum(float(t["trip_amount"]) for t in v_trips) * 30 / max(1, HISTORY_DAYS)

        for t in v_trips:
            amount = float(t["trip_amount"])
            exited = parse_dt(t["exited_at"])
            due = parse_dt(t["payment_due_at"])
            # Сначала часть водителей пополняется заранее: не строго в момент списания.
            safety_threshold = amount * (2.2 if seed.topup_style == "proactive" else 1.05)
            if balance < safety_threshold and (autopay or seed.topup_style == "proactive"):
                if rng.random() < (.80 if seed.topup_style == "proactive" else .55):
                    at = exited - timedelta(hours=rng.randint(2, 18), minutes=rng.randint(0, 59))
                    topup = topup_amount(rng, seed.topup_style, monthly_hint)
                    balance += topup
                    add_tx(all_txs, vid, at, "topup_autopay" if autopay else "topup_manual", "credit", topup, balance)

            # Оплата поездки: обычно сразу после выезда, но иногда ближе к дедлайну.
            if balance >= amount:
                delay_hours = 0 if seed.topup_style == "proactive" else rng.choice([0, 1, 2, 5, 12, 20])
                pay_at = min(exited + timedelta(hours=delay_hours, minutes=rng.randint(0, 45)), due - timedelta(minutes=5))
                balance -= amount
                add_tx(all_txs, vid, pay_at, "trip_charge", "debit", amount, balance, int(t["trip_id"]))
                t["is_paid"] = 1
                continue

            # Денег нет: часть пользователей пополняется до дедлайна, часть уходит в просрочку.
            t["is_paid"] = 0
            before_due_p = {"proactive": .55, "reactive": .64, "negligent": .18}[seed.topup_style]
            if rng.random() < before_due_p:
                at = exited + timedelta(hours=rng.randint(2, max(3, seed.grace_hours - 2)))
                topup = max(topup_amount(rng, seed.topup_style, monthly_hint), amount + rng.randint(50, 350))
                balance += topup
                add_tx(all_txs, vid, at, "topup_autopay" if autopay else "topup_manual", "credit", topup, balance)
                balance -= amount
                add_tx(all_txs, vid, at + timedelta(minutes=rng.randint(1, 20)), "trip_charge", "debit", amount, balance, int(t["trip_id"]))
                t["is_paid"] = 1
            else:
                fine_at = due + timedelta(hours=FINE_DELAY_HOURS_AFTER_DUE)
                balance -= FINE_AMOUNT
                add_tx(all_txs, vid, fine_at, "fine_assessed", "debit", FINE_AMOUNT, balance, int(t["trip_id"]))
                after_due_p = .70 if seed.topup_style != "negligent" else .32
                if rng.random() < after_due_p:
                    at = fine_at + timedelta(hours=rng.randint(3, 96))
                    need = amount + FINE_AMOUNT + rng.randint(50, 400)
                    topup = max(topup_amount(rng, seed.topup_style, monthly_hint), need)
                    balance += topup
                    add_tx(all_txs, vid, at, "topup_autopay" if autopay else "topup_manual", "credit", topup, balance)
                    balance -= amount
                    add_tx(all_txs, vid, at + timedelta(minutes=rng.randint(1, 15)), "trip_charge", "debit", amount, balance, int(t["trip_id"]))
                    balance -= FINE_AMOUNT
                    add_tx(all_txs, vid, at + timedelta(minutes=rng.randint(16, 35)), "fine_paid", "debit", FINE_AMOUNT, balance, int(t["trip_id"]))
                    t["is_paid"] = 1
                else:
                    pending.append(t)

        balances[vid] = money(balance)
        v["current_balance"] = money(balance)
        v["account_status"] = "debt" if any(str(t["is_paid"]) != "1" for t in v_trips) or balance < 0 else "active"

    all_txs.sort(key=lambda tx: (tx["occurred_at"], tx["vehicle_id"], tx["operation_type"]))
    for i, tx in enumerate(all_txs, start=1):
        tx["transaction_id"] = i
        tx["occurred_at"] = fmt_dt(tx["occurred_at"])
    return all_txs, trips, vehicles


def build_features(vehicles: list[dict], trips: list[dict], txs: list[dict]) -> list[dict]:
    as_of = datetime.combine(DATA_END_DATE, time(23, 59, 59))
    w7 = as_of - timedelta(days=7)
    w30 = as_of - timedelta(days=30)
    trips_by_v: dict[int, list[dict]] = {}
    tx_by_v: dict[int, list[dict]] = {}
    for t in trips:
        trips_by_v.setdefault(int(t["vehicle_id"]), []).append(t)
    for tx in txs:
        tx_by_v.setdefault(int(tx["vehicle_id"]), []).append(tx)

    rows = []
    for v in vehicles:
        vid = int(v["vehicle_id"])
        v_trips = trips_by_v.get(vid, [])
        amounts = [float(t["trip_amount"]) for t in v_trips]
        durations = []
        weekend = morning = trips_7d = trips_30d = 0
        for t in v_trips:
            entered = parse_dt(t["entered_at"])
            exited = parse_dt(t["exited_at"])
            durations.append(max(1, int((exited - entered).total_seconds() // 60)))
            if exited >= w7:
                trips_7d += 1
            if exited >= w30:
                trips_30d += 1
            if entered.weekday() >= 5:
                weekend += 1
            if entered.weekday() < 5 and 7 <= entered.hour < 10:
                morning += 1
        topups = [float(tx["amount"]) for tx in tx_by_v.get(vid, []) if parse_dt(tx["occurred_at"]) >= w30 and tx["operation_type"] in ("topup_manual", "topup_autopay")]
        debt_ep = sum(1 for tx in tx_by_v.get(vid, []) if parse_dt(tx["occurred_at"]) >= w30 and float(tx["balance_after"]) < 0)
        fines = sum(1 for tx in tx_by_v.get(vid, []) if parse_dt(tx["occurred_at"]) >= w30 and tx["operation_type"] == "fine_assessed")
        n = len(v_trips)
        row = {
            "vehicle_id": vid,
            "updated_at": fmt_dt(as_of),
            "trips_7d": trips_7d,
            "trips_30d": trips_30d,
            "avg_trip_amount": money(statistics.mean(amounts)) if amounts else "",
            "avg_trip_duration_min": int(statistics.mean(durations)) if durations else "",
            "weekend_trip_share": round(weekend / n, 4) if n else 0,
            "morning_entry_share": round(morning / n, 4) if n else 0,
            "topup_count_30d": len(topups),
            "avg_topup_amount": money(statistics.mean(topups)) if topups else "",
            "debt_episodes_30d": debt_ep,
            "fines_count_30d": fines,
            "days_since_registration": (DATA_END_DATE - date.fromisoformat(v["registered_at"])).days,
            "trip_count_total": n,
        }
        code, name = assign_segment(row)
        row["segment_code"] = code
        row["segment_name"] = name
        row["segment_assigned_at"] = fmt_dt(as_of)
        rows.append(row)
    return rows


def assign_segment(row: dict) -> tuple[str, str]:
    # Rule-based аналитика поверх агрегатов. Истинный archetype из seed здесь НЕ используется.
    total = int(row["trip_count_total"])
    days = int(row["days_since_registration"])
    trips_30d = int(row["trips_30d"])
    trips_7d = int(row["trips_7d"])
    duration = float(row["avg_trip_duration_min"] or 0)
    weekend = float(row["weekend_trip_share"] or 0)
    morning = float(row["morning_entry_share"] or 0)

    if total <= 4 and days <= 21:
        code = "new_user"
    elif trips_30d >= 80 and duration <= 45:
        code = "taxi_driver"
    elif morning >= .45 and trips_30d >= 14 and duration >= 240:
        code = "commuter"
    elif trips_30d <= 12 and total <= 14 and duration >= 180:
        # Туристы могут попадать на event-дни и выходные, поэтому проверяем длинные редкие визиты до weekend-правила.
        code = "tourist"
    elif weekend >= .55 and trips_30d <= 20:
        code = "weekend_guest"
    elif trips_30d <= 14 and duration >= 90:
        code = "tourist"
    elif trips_7d >= 18 and duration <= 50:
        code = "taxi_driver"
    else:
        # Запасной выбор по ближайшему смысловому признаку.
        if morning > .35 and duration > 220:
            code = "commuter"
        elif weekend > .45:
            code = "weekend_guest"
        elif duration < 55 and trips_30d > 30:
            code = "taxi_driver"
        else:
            code = "tourist"
    return code, SEGMENT_NAMES[code]


def subscription_saving_title(seg: str, monthly: float, avg_amt: float, trips_30d: int) -> Optional[tuple[str, str, str]]:
    if seg == "taxi_driver" and trips_30d >= 30:
        price = SUBSCRIPTION_PRICES["trip_pack_30"]
        saving = monthly - price
        if saving > max(250, price * .10):
            return (
                "trip_pack_30",
                f"Пакет 30 поездок может сэкономить около {saving:.0f} ₽ в месяц",
                "app://buy_subscription?type=trip_pack_30",
            )
    if seg == "commuter" and trips_30d >= 14:
        price = SUBSCRIPTION_PRICES["monthly_unlimited"]
        saving = monthly - price
        if saving > max(300, price * .10):
            return (
                "monthly_unlimited",
                f"Месячный абонемент может сэкономить около {saving:.0f} ₽",
                "app://buy_subscription?type=monthly_unlimited",
            )
    if seg == "weekend_guest":
        price = SUBSCRIPTION_PRICES["weekend_pack"]
        saving = monthly - price
        if saving > max(150, price * .08):
            return (
                "weekend_pack",
                f"Weekend pack может сэкономить около {saving:.0f} ₽ на выходных поездках",
                "app://buy_subscription?type=weekend_pack",
            )
    if seg == "tourist" and avg_amt > SUBSCRIPTION_PRICES["daily_unlimited"] * 1.05:
        saving = avg_amt - SUBSCRIPTION_PRICES["daily_unlimited"]
        return (
            "daily_unlimited",
            f"Суточный безлимит выгоднее длительного визита: экономия около {saving:.0f} ₽",
            "app://buy_subscription?type=daily_unlimited",
        )
    if seg == "new_user" and trips_30d >= 3:
        price = SUBSCRIPTION_PRICES["trip_pack_10"]
        expected = avg_amt * 10
        saving = expected - price
        if saving > 100:
            return (
                "trip_pack_10",
                f"Пакет 10 поездок снизит среднюю стоимость въезда: выгода около {saving:.0f} ₽",
                "app://buy_subscription?type=trip_pack_10",
            )
    return None


def candidate_recommendations(v: dict, feat: dict) -> list[dict]:
    seg = feat["segment_code"]
    balance = float(v["current_balance"])
    avg_amt = float(feat["avg_trip_amount"] or 100)
    trips_30d = int(feat["trips_30d"])
    monthly = trips_30d * avg_amt
    items = []
    if v["account_status"] == "debt" or int(feat["fines_count_30d"]) > 0:
        items.append(("repay_debt", "Погасите задолженность, чтобы избежать блокировки", "app://repay_debt", 10))
    if balance < max(350, avg_amt * 2):
        amount = int(clamp(max(500, avg_amt * 3), 500, 2000) // 50 * 50)
        items.append(("topup_balance", f"Баланс {balance:.0f} ₽ — пополните счёт", f"app://topup?amount={amount}", 8))
    if seg in ("commuter", "taxi_driver", "weekend_guest") and monthly > 0 and balance < monthly * .22:
        amount = int(clamp(monthly * .25, 500, 3000) // 50 * 50)
        items.append(("topup_forecast", f"На месяц прогноз ~{monthly:.0f} ₽ — пополните заранее", f"app://topup?amount={amount}", 7))
    if int(v["autopay_enabled"]) == 0 and seg in ("commuter", "taxi_driver"):
        items.append(("enable_autopay", "Подключите автоплатёж для регулярных поездок", "app://enable_autopay", 6))
    if int(v["has_subscription"]) == 0:
        sub_offer = subscription_saving_title(seg, monthly, avg_amt, trips_30d)
        if sub_offer:
            _, title, link = sub_offer
            items.append(("buy_subscription", title, link, 5))
    if seg == "new_user":
        items.append(("pay_before_deadline", "Оплачивайте поездки до срока, чтобы избежать штрафа", "app://trips", 4))
    return [{"type": t, "title": title, "deep_link": link, "priority": pr} for t, title, link, pr in sorted(items, key=lambda x: -x[3])]

def accept_probability(seed: DriverSeed, seg: str, rtype: str, status_debt: bool) -> float:
    base = {
        "repay_debt": .58,
        "topup_balance": .52,
        "topup_forecast": .48,
        "enable_autopay": .36,
        "buy_subscription": .34,
        "pay_before_deadline": .46,
    }.get(rtype, .40)
    if status_debt and rtype in ("repay_debt", "topup_balance"):
        base += .16
    if seed.topup_style == "proactive":
        base += .10
    elif seed.topup_style == "negligent":
        base -= .12
    base += (seed.app_engagement - .5) * .25
    return clamp(base, .06, .90)




def recommendation_shown_at(rng: random.Random, recommendation_type: str) -> datetime:
    """Часть рекомендаций показываем перед event-днями, чтобы были общие всплески спроса и советов."""
    min_day = DATA_END_DATE - timedelta(days=18)
    max_day = DATA_END_DATE - timedelta(days=2)
    event_leads: list[date] = []
    if recommendation_type in ("topup_forecast", "topup_balance"):
        for event_day in EVENT_DAYS:
            for lead in (1, 2):
                shown_day = event_day - timedelta(days=lead)
                if min_day <= shown_day <= max_day:
                    event_leads.append(shown_day)
        if event_leads and rng.random() < .55:
            shown_day = rng.choice(event_leads)
            minute = normal_minutes(rng, 18 * 60, 120, 10 * 60, 21 * 60)
            return datetime.combine(shown_day, time(minute // 60, minute % 60, rng.randint(0, 59)))
    if recommendation_type == "buy_subscription":
        for event_day in EVENT_DAYS:
            for lead in (3, 4, 5):
                shown_day = event_day - timedelta(days=lead)
                if min_day <= shown_day <= max_day:
                    event_leads.append(shown_day)
        if event_leads and rng.random() < .35:
            shown_day = rng.choice(event_leads)
            minute = normal_minutes(rng, 15 * 60, 150, 9 * 60, 21 * 60)
            return datetime.combine(shown_day, time(minute // 60, minute % 60, rng.randint(0, 59)))

    shown_day = DATA_END_DATE - timedelta(days=rng.randint(2, 18))
    minute = normal_minutes(rng, 13 * 60, 170, 8 * 60, 21 * 60)
    return datetime.combine(shown_day, time(minute // 60, minute % 60, rng.randint(0, 59)))


def make_recommendations_and_actions(rng: random.Random, vehicles: list[dict], features: list[dict], txs: list[dict], seeds: list[DriverSeed]) -> tuple[list[dict], list[dict], list[dict]]:
    seed_by_id = {s.vehicle_id: s for s in seeds}
    feat_by_id = {int(f["vehicle_id"]): f for f in features}
    # Баланс начинаем с текущего после первичной истории.
    balance_by_id = {int(v["vehicle_id"]): float(v["current_balance"]) for v in vehicles}
    events: list[dict] = []
    event_id = 1
    rec_action_txs: list[dict] = []

    for v in vehicles:
        vid = int(v["vehicle_id"])
        seed = seed_by_id[vid]
        feat = feat_by_id[vid]
        cands = candidate_recommendations(v, feat)
        if not cands:
            continue
        rng.shuffle(cands)
        max_events = 2 if seed.archetype in ("new_user", "tourist") else 3
        chosen = sorted(cands[:rng.randint(1, min(max_events, len(cands)))], key=lambda x: -x["priority"])
        for cand in chosen:
            shown_at = recommendation_shown_at(rng, cand["type"])
            p = accept_probability(seed, feat["segment_code"], cand["type"], v["account_status"] == "debt")
            roll = rng.random()
            if roll < p:
                status = "accepted"
                responded_at = shown_at + timedelta(minutes=rng.randint(3, 180))
            elif roll < p + .18:
                status = "dismissed"
                responded_at = shown_at + timedelta(minutes=rng.randint(1, 80))
            elif roll < p + .30:
                status = "shown"
                responded_at = None
            else:
                status = "expired"
                responded_at = shown_at + timedelta(days=rng.randint(1, 3), hours=rng.randint(0, 6))

            ev = {
                "event_id": event_id,
                "vehicle_id": vid,
                "shown_at": shown_at,
                "recommendation_type": cand["type"],
                "title": cand["title"],
                "status": status,
                "responded_at": responded_at,
                "deep_link": cand["deep_link"],
                "related_transaction_id": "",
            }

            if status == "accepted":
                bal = balance_by_id[vid]
                tx = None
                if cand["type"] in ("topup_balance", "topup_forecast", "repay_debt"):
                    amount = 500.0 if cand["type"] == "topup_balance" else topup_amount(rng, seed.topup_style, float(feat["trips_30d"] or 1) * float(feat["avg_trip_amount"] or 100))
                    if cand["type"] == "repay_debt":
                        amount = max(amount, abs(min(0, bal)) + 600)
                    bal += amount
                    tx = add_tx(rec_action_txs, vid, responded_at, "topup_autopay" if int(v["autopay_enabled"]) else "topup_manual", "credit", amount, bal, recommendation_event_id=event_id)
                elif cand["type"] == "buy_subscription":
                    if "weekend_pack" in cand["deep_link"]:
                        sub_type = "weekend_pack"
                    elif "monthly_unlimited" in cand["deep_link"]:
                        sub_type = "monthly_unlimited"
                    elif "daily_unlimited" in cand["deep_link"]:
                        sub_type = "daily_unlimited"
                    elif "trip_pack_10" in cand["deep_link"]:
                        sub_type = "trip_pack_10"
                    else:
                        sub_type = "trip_pack_30"
                    cost = SUBSCRIPTION_PRICES[sub_type]
                    if bal < cost:
                        top = cost - bal + 300
                        bal += top
                        add_tx(rec_action_txs, vid, responded_at - timedelta(minutes=3), "topup_manual", "credit", top, bal, recommendation_event_id=event_id)
                    bal -= cost
                    tx = add_tx(rec_action_txs, vid, responded_at, "subscription_purchase", "debit", cost, bal, recommendation_event_id=event_id)
                    v["has_subscription"] = 1
                    v["subscription_type"] = sub_type
                    v["subscription_valid_until"] = (DATA_END_DATE + timedelta(days=1 if sub_type == "daily_unlimited" else 30)).isoformat()
                elif cand["type"] == "enable_autopay":
                    v["autopay_enabled"] = 1
                balance_by_id[vid] = money(bal)
                v["current_balance"] = money(bal)
                if tx is not None:
                    ev["_linked_tx_object"] = tx
            events.append(ev)
            event_id += 1

    # Хронологически сортируем рекомендации, но event_id оставляем как технический идентификатор показа.
    txs.extend(rec_action_txs)
    txs.sort(key=lambda tx: (tx["occurred_at"] if isinstance(tx["occurred_at"], datetime) else parse_dt(tx["occurred_at"]), tx["vehicle_id"], tx["operation_type"]))
    for i, tx in enumerate(txs, start=1):
        tx["transaction_id"] = i
        if isinstance(tx["occurred_at"], datetime):
            tx["occurred_at"] = fmt_dt(tx["occurred_at"])
    for ev in events:
        linked = ev.pop("_linked_tx_object", None)
        if linked:
            ev["related_transaction_id"] = linked["transaction_id"]
        ev["shown_at"] = fmt_dt(ev["shown_at"])
        ev["responded_at"] = fmt_dt(ev["responded_at"]) if ev["responded_at"] else ""
    events.sort(key=lambda ev: (ev["shown_at"], ev["vehicle_id"]))
    for v in vehicles:
        v["account_status"] = "debt" if float(v["current_balance"]) < 0 else v["account_status"]
    return events, txs, vehicles


def evaluate_segments(seeds: list[DriverSeed], features: list[dict]) -> dict:
    true_by_id = {s.vehicle_id: s.archetype for s in seeds}
    total = len(features)
    correct = sum(1 for f in features if true_by_id[int(f["vehicle_id"])] == f["segment_code"])
    by_true = {}
    for f in features:
        true = true_by_id[int(f["vehicle_id"])]
        pred = f["segment_code"]
        by_true.setdefault(true, {})[pred] = by_true.setdefault(true, {}).get(pred, 0) + 1
    return {"total": total, "correct": correct, "accuracy": round(correct / total, 3), "confusion": by_true}


def generate_all(output_dir: Path = OUTPUT_DIR, seed: int = RANDOM_SEED) -> dict:
    rng = random.Random(seed)
    output_dir.mkdir(parents=True, exist_ok=True)
    seeds = build_seeds(rng)
    vehicles = make_vehicles(rng, seeds)
    trips = make_trips(rng, seeds, vehicles)
    txs, trips, vehicles = simulate_transactions(rng, vehicles, trips, seeds)
    features = build_features(vehicles, trips, txs)
    events, txs, vehicles = make_recommendations_and_actions(rng, vehicles, features, txs, seeds)
    # После действий по рекомендациям пересчитаем признаки по финальной истории операций.
    features = build_features(vehicles, trips, txs)
    report = evaluate_segments(seeds, features)

    write_csv(output_dir / "vehicles.csv", vehicles, VEHICLE_FIELDS)
    write_csv(output_dir / "trips.csv", trips, TRIP_FIELDS)
    write_csv(output_dir / "account_transactions.csv", txs, TX_FIELDS)
    write_csv(output_dir / "vehicle_behavior_features.csv", features, FEATURE_FIELDS)
    write_csv(output_dir / "recommendation_events.csv", events, RECO_FIELDS)
    (output_dir / "driver_seeds.json").write_text(json.dumps([asdict(s) for s in seeds], ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "quality_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "vehicles": len(vehicles),
        "trips": len(trips),
        "transactions": len(txs),
        "recommendations": len(events),
        "segment_accuracy": report["accuracy"],
        "confusion": report["confusion"],
    }


if __name__ == "__main__":
    print(json.dumps(generate_all(), ensure_ascii=False, indent=2))
