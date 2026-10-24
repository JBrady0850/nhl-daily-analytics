"""When the Daily data workflow should build.

GitHub starts scheduled workflows on a best-effort basis: a run can start hours
late, or not at all (the 14:05 UTC run of 2026-10-24 never started). So the
workflow has two main update times plus backup times after each. A scheduled run
builds only when the data file is older than the most recent main update time;
otherwise the update already happened and the run stops in a few seconds.

    python -m nhlda.schedule app/data/latest.json      # prints build=true or build=false

Standard library only, so it runs before the build's requirements are installed.
The app (RUNS_UTC in app/js/app.js) and the cron lines in
.github/workflows/daily.yml must list the same main times; a test checks this.
"""
import datetime as dt
import json
import sys

MAIN_UTC = ((14, 5), (22, 20))          # the two daily updates, hour and minute in UTC


def last_main(now):
    """The most recent main update time at or before now (a UTC datetime)."""
    day = now.astimezone(dt.timezone.utc).date()
    times = [dt.datetime(d.year, d.month, d.day, h, m, tzinfo=dt.timezone.utc)
             for d in (day - dt.timedelta(days=1), day) for h, m in MAIN_UTC]
    return max(t for t in times if t <= now)


def parse_utc(text):
    """An ISO time such as 2026-10-24T15:15:51Z, or None when missing or unreadable."""
    try:
        t = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def due(generated_utc, now=None):
    """(build, reason). Build when the data predates the latest main update time,
    or when the file has no readable build time."""
    now = now or dt.datetime.now(dt.timezone.utc)
    slot = last_main(now)
    built = parse_utc(generated_utc)
    if built is None:
        return True, "the data file has no readable build time"
    if built < slot:
        return True, f"data built {built:%Y-%m-%d %H:%M}Z, before the {slot:%H:%M}Z update"
    return False, f"data built {built:%Y-%m-%d %H:%M}Z already covers the {slot:%H:%M}Z update"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else "app/data/latest.json"
    try:
        with open(path, encoding="utf-8") as fh:
            generated = json.load(fh).get("generated_utc")
    except (OSError, ValueError):
        generated = None
    build, reason = due(generated)
    print(f"build={'true' if build else 'false'}")
    print(("Build: " if build else "Skip: ") + reason, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
