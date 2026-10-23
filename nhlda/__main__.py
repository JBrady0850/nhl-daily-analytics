"""Command line: python -m nhlda build | check

  python -m nhlda build [--date YYYY-MM-DD] [--state state] [--out app/data] [--teams FLA,TBL] [--no-dfo]
  python -m nhlda check app/data/latest.json
"""
import argparse
import asyncio
import datetime as dt
import json
import logging
import sys

from .build import build, check_public, FORMAT


def today_central():
    """The slate date on the league's clock: US Central, which is where the
    schedule's day boundary sits for a morning run."""
    now = dt.datetime.now(dt.timezone.utc)
    return (now - dt.timedelta(hours=6)).date().isoformat()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nhlda", description="NHL Daily Analytics data build")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="price today's slate and write the app data file")
    b.add_argument("--date", default=None)
    b.add_argument("--state", default="state")
    b.add_argument("--out", default="app/data")
    b.add_argument("--teams", default=None, help="comma list; default all 32")
    b.add_argument("--no-dfo", action="store_true", help="skip Daily Faceoff (goalies and lines)")
    c = sub.add_parser("check", help="validate a data file")
    c.add_argument("path")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if a.cmd == "build":
        date = a.date or today_central()
        teams = [t.strip().upper() for t in a.teams.split(",")] if a.teams else None
        out = asyncio.run(build(date, a.state, a.out, teams=teams, use_dfo=not a.no_dfo))
        print(json.dumps({"date": out["date"], "games": out["slate"]["game_count"],
                          "lines": out["sources"].get("lines"), "problems": out["problems"],
                          "seconds": out["sources"].get("seconds")}, indent=1))
        return 0
    data = json.load(open(a.path, encoding="utf-8"))
    if data.get("format") != FORMAT:
        print(f"wrong format: {data.get('format')}")
        return 1
    check_public(data)
    print(f"ok: {a.path} ({data['date']}, {data['slate']['game_count']} games, {len(data.get('lines') or {})} teams)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
