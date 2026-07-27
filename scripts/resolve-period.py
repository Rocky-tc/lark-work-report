#!/usr/bin/env python3
"""Resolve daily, weekly, monthly, and custom report periods deterministically."""

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


def iso_midnight(value, timezone):
    return datetime.combine(value, time.min, tzinfo=timezone).isoformat()


def resolve(period, reference, start=None, end=None, timezone_name="Asia/Shanghai"):
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {timezone_name}") from exc

    if period == "daily":
        period_start = reference
        period_end = reference + timedelta(days=1)
        comparison_start = period_start - timedelta(days=1)
        comparison_end = period_start
        title_period = period_start.isoformat()
    elif period == "weekly":
        period_start = reference - timedelta(days=reference.weekday())
        period_end = period_start + timedelta(days=7)
        comparison_start = period_start - timedelta(days=7)
        comparison_end = period_start
        title_period = f"{period_start.isoformat()} 至 {(period_end - timedelta(days=1)).isoformat()}"
    elif period == "monthly":
        period_start = month_start(reference)
        period_end = next_month(period_start)
        comparison_start = previous_month(period_start)
        comparison_end = period_start
        title_period = f"{period_start.year}年{period_start.month}月"
    else:
        if start is None or end is None:
            raise ValueError("custom period requires --start and --end")
        if start > end:
            raise ValueError("start must be on or before end")
        period_start = start
        period_end = end + timedelta(days=1)
        duration = period_end - period_start
        comparison_start = period_start - duration
        comparison_end = period_start
        title_period = f"{start.isoformat()} 至 {end.isoformat()}"

    return {
        "period": period,
        "start": iso_midnight(period_start, timezone),
        "end": iso_midnight(period_end, timezone),
        "comparison_start": iso_midnight(comparison_start, timezone),
        "comparison_end": iso_midnight(comparison_end, timezone),
        "timezone": timezone_name,
        "snapshot_date": reference.isoformat(),
        "title_period": title_period,
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", required=True, choices=("daily", "weekly", "monthly", "custom"))
    parser.add_argument("--reference", required=True, help="Reference date in YYYY-MM-DD")
    parser.add_argument("--start", help="Inclusive custom start date in YYYY-MM-DD")
    parser.add_argument("--end", help="Inclusive custom end date in YYYY-MM-DD")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        payload = resolve(
            args.period,
            parse_date(args.reference),
            parse_date(args.start) if args.start else None,
            parse_date(args.end) if args.end else None,
            args.timezone,
        )
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
