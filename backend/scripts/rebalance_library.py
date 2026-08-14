"""Prune filler content from the library to free budget for real movies.

Keeps videos that are in curated film collections or have an IMDb id
(they were stored by the crawler at index time), deletes everything else
(educational / industrial / news / TV shorts), along with their dialogue.

Usage (from backend/):
    python scripts/rebalance_library.py            # analysis only
    python scripts/rebalance_library.py --apply    # actually delete
    python scripts/rebalance_library.py --keep prelinger
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

KEEP_COLLECTIONS = {
    "feature_films", "classic_films", "silent_films", "moviesandfilms",
    "feature_films_unsorted", "b-movies", "monsters_archive",
}
EXTRA_COLLECTIONS = {"prelinger"}  # opt-in via --keep


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="delete filler (default: analysis only)")
    parser.add_argument("--keep", action="append", default=[], help="extra collection to keep")
    args = parser.parse_args()

    from pymongo import MongoClient

    keep_colls = KEEP_COLLECTIONS | set(args.keep)
    mongo = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=15000)
    db = mongo[settings.mongodb_database]

    videos = list(db.videos.find({}, {"_id": 1, "title": 1, "collection": 1, "imdbId": 1}))
    seg_counts = {
        d["_id"]: d["n"]
        for d in db.dialogue.aggregate(
            [{"$group": {"_id": "$videoId", "n": {"$sum": 1}}}]
        )
    }
    total_segs = sum(seg_counts.values())

    keep_ids, drop_ids = [], []
    keep_segs = drop_segs = 0
    for v in videos:
        colls = {c.lower() for c in (v.get("collection") or [])}
        keep = bool(colls & keep_colls) or bool(v.get("imdbId"))
        segs = seg_counts.get(str(v["_id"]), 0)
        if keep:
            keep_ids.append(v["_id"])
            keep_segs += segs
        else:
            drop_ids.append(v["_id"])
            drop_segs += segs

    print(f"Library: {len(videos)} videos / {total_segs} segments")
    print(f"  keep: {len(keep_ids)} videos / {keep_segs} segments")
    print(f"  drop: {len(drop_ids)} videos / {drop_segs} segments ({drop_segs / total_segs:.1%})")

    if not args.apply:
        print("\nDry run - add --apply to delete the filler.")
        return

    drop_strs = [str(vid) for vid in drop_ids]
    res = db.dialogue.delete_many({"videoId": {"$in": drop_strs}})
    db.videos.delete_many({"_id": {"$in": drop_ids}})
    print(f"\nDeleted {len(drop_ids)} videos / {res.deleted_count} segments. "
          f"Library now {db.videos.count_documents({})} videos / {db.dialogue.count_documents({})} segments")


if __name__ == "__main__":
    main()