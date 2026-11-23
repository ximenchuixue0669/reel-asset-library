"""Draw daily Codex thread counts from a local, read-only thread index."""

import argparse
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

PALETTE = ["#e6edf6", "#b5dfd4", "#6bc6af", "#248f81", "#126356"]
INK = "#1b3245"
SECONDARY = "#466279"


def counts_by_day(source: Path, first: date, last: date, zone: ZoneInfo) -> Counter[date]:
    lower = int(datetime.combine(first, time(), zone).timestamp())
    upper = int(datetime.combine(last + timedelta(days=1), time(), zone).timestamp())
    uri = f"{source.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as database:
        starts = database.execute(
            "SELECT created_at FROM threads WHERE created_at >= ? AND created_at < ?",
            (lower, upper),
        )
        return Counter(datetime.fromtimestamp(row[0], zone).date() for row in starts)


def heat_color(count: int) -> str:
    return PALETTE[sum(count >= threshold for threshold in (1, 3, 6, 11))]


def draw_heatmap(counts: Counter, first: date, last: date, zone: ZoneInfo, output: Path) -> None:
    step = 44
    grid_x, grid_y = 136, 146
    monday = first - timedelta(days=first.weekday())
    weeks = (last - monday).days // 7 + 1
    width = max(920, grid_x + step * weeks + 56)
    image = Image.new("RGB", (width, 545), "#f8fafc")
    canvas = ImageDraw.Draw(image)
    title = ImageFont.load_default(size=38)
    copy = ImageFont.load_default(size=21)
    caption = ImageFont.load_default(size=16)
    canvas.text((44, 32), "Codex activity", fill=INK, font=title)
    canvas.text(
        (46, 83),
        f"{first:%b %d, %Y} to {last:%b %d, %Y}  /  {zone.key}",
        fill=SECONDARY,
        font=caption,
    )
    for index, label in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
        canvas.text((68, grid_y + index * step + 8), label, fill=SECONDARY, font=caption)
    day = monday
    last_month = None
    while day <= last:
        offset = (day - monday).days
        column, row = divmod(offset, 7)
        x = grid_x + column * step
        y = grid_y + row * step
        if day >= first:
            if day.month != last_month:
                canvas.text((x, grid_y - 29), day.strftime("%b"), fill=INK, font=caption)
                last_month = day.month
            canvas.rounded_rectangle((x, y, x + 34, y + 34), radius=7, fill=heat_color(counts[day]))
        day += timedelta(days=1)

    canvas.line((44, 463, width - 44, 463), fill="#d6e1ed", width=1)
    canvas.text(
        (46, 488),
        f"{sum(counts.values())} chats started across {len(counts)} days",
        fill=INK,
        font=copy,
    )
    x = width - 306
    for color, label in zip(PALETTE, ("0", "1-2", "3-5", "6-10", "11+"), strict=True):
        canvas.rounded_rectangle((x, 481, x + 19, 500), radius=4, fill=color)
        canvas.text((x, 505), label, fill=SECONDARY, font=caption)
        x += 51
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--from", dest="first", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="last", type=date.fromisoformat, required=True)
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= (args.last - args.first).days + 1 <= 120:
        parser.error("Choose a range of 1–120 days.")
    zone = ZoneInfo(args.timezone)
    counts = counts_by_day(args.index, args.first, args.last, zone)
    draw_heatmap(counts, args.first, args.last, zone, args.output)
    print(f"Wrote heatmap for {sum(counts.values())} chats across {len(counts)} days.")


if __name__ == "__main__":
    main()
