#!/usr/bin/env python3
"""Resolve report periods, partial windows, and comparison windows deterministically."""

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_date(value):
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid date: {value}; expected YYYY-MM-DD") from exc


def parse_datetime(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid snapshot: {value}; expected timezone-aware ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("snapshot must include a timezone offset")
    return parsed


def month_start(value):
    return value.replace(day=1)


def next_month(value):
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def previous_month(value):
    if value.month == 1:
        return date(value.year - 1, 12, 1)
    return date(value.year, value.month - 1, 1)


def at_midnight(value, timezone):
    return datetime.combine(value, time.min, tzinfo=timezone)


def route_profile(duration_days):
    if duration_days <= 2:
        return "daily"
    if duration_days <= 14:
        return "weekly"
    return "monthly"


def resolve(
    period,
    reference,
    start=None,
    end=None,
    timezone_name="Asia/Shanghai",
    relative="current",
    snapshot=None,
):
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {timezone_name}") from exc

    if period == "custom" and relative != "current":
        raise ValueError("custom period does not support --relative previous")

    if period == "daily":
        period_start = reference
        if relative == "previous":
            period_start -= timedelta(days=1)
        natural_end = period_start + timedelta(days=1)
        comparison_start = period_start - timedelta(days=1)
        title_period = period_start.isoformat()
        routed_profile = "daily"
    elif period == "weekly":
        period_start = reference - timedelta(days=reference.weekday())
        if relative == "previous":
            period_start -= timedelta(days=7)
        natural_end = period_start + timedelta(days=7)
        comparison_start = period_start - timedelta(days=7)
        title_period = (
            f"{period_start.isoformat()} 至 "
            f"{(natural_end - timedelta(days=1)).isoformat()}"
        )
        routed_profile = "weekly"
    elif period == "monthly":
        period_start = month_start(reference)
        if relative == "previous":
            period_start = previous_month(period_start)
        natural_end = next_month(period_start)
        comparison_start = previous_month(period_start)
        title_period = f"{period_start.year}年{period_start.month}月"
        routed_profile = "monthly"
    else:
        if start is None or end is None:
            raise ValueError("custom period requires --start and --end")
        if start > end:
            raise ValueError("start must be on or before end")
        period_start = start
        natural_end = end + timedelta(days=1)
        duration = natural_end - period_start
        comparison_start = period_start - duration
        title_period = f"{start.isoformat()} 至 {end.isoformat()}"
        routed_profile = route_profile(duration.days)

    start_time = at_midnight(period_start, timezone)
    natural_end_time = at_midnight(natural_end, timezone)
    comparison_start_time = at_midnight(comparison_start, timezone)
    snapshot_time = snapshot.astimezone(timezone) if snapshot else None

    if snapshot_time is not None and snapshot_time <= start_time:
        raise ValueError("snapshot must be after period start")

    effective_end = natural_end_time
    if snapshot_time is not None and snapshot_time < natural_end_time:
        effective_end = snapshot_time

    elapsed = effective_end - start_time
    comparison_end_time = min(comparison_start_time + elapsed, start_time)

    return {
        "period": period,
        "relative": relative,
        "routed_profile": routed_profile,
        "start": start_time.isoformat(),
        "end": effective_end.isoformat(),
        "natural_end": natural_end_time.isoformat(),
        "comparison_start": comparison_start_time.isoformat(),
        "comparison_end": comparison_end_time.isoformat(),
        "timezone": timezone_name,
        "snapshot_time": snapshot_time.isoformat() if snapshot_time else None,
        "is_partial": effective_end < natural_end_time,
        "title_period": title_period,
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", required=True, choices=("daily", "weekly", "monthly", "custom"))
    parser.add_argument("--reference", required=True, help="Reference date in YYYY-MM-DD")
    parser.add_argument("--relative", choices=("current", "previous"), default="current")
    parser.add_argument("--snapshot", help="Timezone-aware ISO 8601 collection snapshot")
    parser.add_argument("--start", help="Inclusive custom start date in YYYY-MM-DD")
    parser.add_argument("--end", help="Inclusive custom end date in YYYY-MM-DD")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        payload = resolve(
            period=args.period,
            reference=parse_date(args.reference),
            start=parse_date(args.start) if args.start else None,
            end=parse_date(args.end) if args.end else None,
            timezone_name=args.timezone,
            relative=args.relative,
            snapshot=parse_datetime(args.snapshot) if args.snapshot else None,
        )
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
