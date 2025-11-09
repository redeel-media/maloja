"""
Album Inference and Assignment

This module handles album inference from scrobble metadata. After migration refactoring
(Phase 7 - Task 7.3), album guessing operations are consolidated here.

## Album Inference

Album inference analyzes scrobble metadata to automatically assign albums to tracks that
don't have album information. This is particularly useful for:
- Imported scrobbles from services that include album metadata (Last.fm, Spotify, etc.)
- Backfilling album data for older tracks
- Correcting album assignments when `replace=True`

## Album Guessing Algorithm

The guess_albums() function implements a voting-based album inference:

1. **Data Collection**
   - Query scrobbles for tracks that have metadata (`extra` or `rawscrobble`)
   - Filter to tracks without albums (unless `replace=True`)
   - Extract album title and album artists from JSON fields

2. **Vote Counting**
   - For each track, count how many scrobbles mention each album
   - Album identity = tuple of (album_artists..., album_title)
   - Track with 3 scrobbles: 2 mention "Abbey Road" + 1 mentions "Let It Be"
   - Winner: "Abbey Road" (2 votes)

3. **Threshold Filtering**
   - Only assign albums that appear >= MIN_NUM_TO_ASSIGN times (default: 1)
   - Prevents spurious assignments from single incorrect scrobbles
   - Higher threshold = more conservative assignments

4. **Artist Inference**
   - If album has no artists in metadata, use track's artists
   - Assumes album artists match track artists (reasonable default)
   - Returns artist names for downstream album creation

## Function

### guess_albums(track_ids=None, replace=False, dbconn=None)
Infer album assignments from scrobble metadata.

**Parameters:**
- `track_ids` (list[int], optional): Only analyze these tracks. If None, analyze all tracks.
- `replace` (bool): If False (default), only process tracks without albums. If True, reanalyze all tracks.
- `dbconn`: Database connection (provided by @connection_provider)

**Returns:**
Dict mapping track_id → assignment result:
```python
{
    123: {
        "assigned": {
            "artists": ["The Beatles"],
            "albumtitle": "Abbey Road"
        }
        # Optional: "guess_artists": [] (empty means use album_artists)
    },
    456: {
        "assigned": False,
        "reason": "Not enough data"  # or "No scrobbles with album information found"
    }
}
```

**Assignment Result Structure:**

Success case:
- `assigned`: Dict with `artists` (list) and `albumtitle` (str)
- `guess_artists` (optional): List of artist names to use if `artists` is empty

Failure cases:
- `assigned`: False
- `reason`: String explaining why assignment failed
  - "Not enough data" - Album appeared but < MIN_NUM_TO_ASSIGN times
  - "No scrobbles with album information found" - No metadata available

**Example Usage:**
```python
# Guess albums for all tracks without albums
results = guess_albums()

# Guess albums for specific tracks
results = guess_albums(track_ids=[123, 456, 789])

# Re-guess albums even for tracks that already have albums
results = guess_albums(replace=True)

# Process results
for track_id, result in results.items():
    if result["assigned"]:
        album_info = result["assigned"]
        artists = album_info["artists"]
        if not artists and "guess_artists" in result:
            artists = result["guess_artists"]
        albumtitle = album_info["albumtitle"]
        print(f"Track {track_id} → Album '{albumtitle}' by {artists}")
    else:
        print(f"Track {track_id} → {result['reason']}")
```

**Metadata Sources:**

The function checks two JSON fields in scrobbles table:
1. `extra` - Additional metadata provided by scrobble source
2. `rawscrobble` - Original raw scrobble data

Both fields are checked for these keys:
- `album_name` or `album_title` - Album title
- `album_artists` - List of album artist names

**Algorithm Details:**

1. Join scrobbles with tracks to get track_id
2. Filter scrobbles that have metadata (extra IS NOT NULL OR rawscrobble IS NOT NULL)
3. Optionally filter by track_ids
4. Optionally filter to tracks without albums (album_id IS NULL)
5. For each scrobble, extract album info from JSON
6. Count album mentions per track
7. For each track, pick album with most mentions (if >= threshold)
8. For albums without artists, query track's artists as fallback
9. Return assignment results

**Downstream Usage:**

Callers typically use this function to:
1. Call guess_albums() to get assignment recommendations
2. For each successful assignment, create or find album via add_album_to_track()
3. Update track's album_id foreign key

**Performance Considerations:**

- Scans all scrobbles for tracks (or specified track_ids)
- Parses JSON for every scrobble with metadata
- Single database query + one query for artist fallbacks
- No caching (designed for batch processing)

**No Cache Invalidation:**

This function does NOT modify the database or call reset_id_caches().
It only returns recommendations. The caller is responsible for:
- Creating/finding albums
- Updating track records
- Invalidating caches after modifications

## Related Modules

- `core.schema`: DB table definitions (scrobbles, tracks, trackartists, artists)
- `core.connection`: connection_provider decorator

## Migration Notes

**Phase 7 - Task 7.3**: Extracted from database/sqldb.py (lines 514-609)
- No cache invalidation needed (read-only analysis function)
- Imports json module for parsing scrobble metadata
- Uses @connection_provider decorator
- MIN_NUM_TO_ASSIGN threshold prevents spurious assignments
"""

import json
import sqlalchemy as sql

from ..core.connection import connection_provider
from ..core.schema import DB


@connection_provider
def guess_albums(track_ids=None, replace=False, dbconn=None):
	"""
	Infer album assignments from scrobble metadata.

	Analyzes scrobble metadata (extra and rawscrobble JSON fields) to determine
	which album each track belongs to based on voting. Returns assignment
	recommendations without modifying the database.

	Args:
		track_ids: Optional list of track IDs to analyze. If None, analyzes all tracks.
		replace: If False (default), only process tracks without albums.
		         If True, reanalyze all tracks including those with albums.
		dbconn: Database connection (provided by decorator)

	Returns:
		Dict mapping track_id to assignment result:
		- Success: {"assigned": {"artists": [...], "albumtitle": "..."}, "guess_artists": [...]}
		- Failure: {"assigned": False, "reason": "..."}

	Algorithm:
		1. Query scrobbles with metadata for specified tracks
		2. Count album mentions per track from JSON fields
		3. Assign album with most mentions (if >= MIN_NUM_TO_ASSIGN)
		4. For albums without artists, use track's artists as fallback
		5. Return recommendations for caller to process

	Note:
		Does NOT modify database. Caller must create albums and update tracks.
		Does NOT invalidate caches (read-only analysis).
	"""

	MIN_NUM_TO_ASSIGN = 1

	jointable = sql.join(
		DB['scrobbles'],
		DB['tracks']
	)

	# get all scrobbles of the respective tracks that have some info
	conditions = [
		DB['scrobbles'].c.extra.isnot(None) | DB['scrobbles'].c.rawscrobble.isnot(None)
	]
	if track_ids is not None:
		# only do these tracks
		conditions.append(
			DB['scrobbles'].c.track_id.in_(track_ids)
		)
	if not replace:
		# only tracks that have no album yet
		conditions.append(
			DB['tracks'].c.album_id.is_(None)
		)

	op = sql.select(
		DB['scrobbles']
	).select_from(jointable).where(
		*conditions
	)

	result = dbconn.execute(op).all()

	# for each track, count what album info appears how often
	possible_albums = {}
	for row in result:
		albumtitle, albumartists = None, None
		if row.extra:
			extrainfo = json.loads(row.extra)
			albumtitle = extrainfo.get("album_name") or extrainfo.get("album_title")
			albumartists = extrainfo.get("album_artists", [])
		if not albumtitle:
			# either we didn't have info in the exta col, or there was no albumtitle
			# try the raw scrobble
			extrainfo = json.loads(row.rawscrobble)
			albumtitle = extrainfo.get("album_name") or extrainfo.get("album_title")
			albumartists = albumartists or extrainfo.get("album_artists", [])
		if albumtitle:
			hashable_albuminfo = tuple([*albumartists, albumtitle])
			possible_albums.setdefault(row.track_id, {}).setdefault(hashable_albuminfo, 0)
			possible_albums[row.track_id][hashable_albuminfo] += 1

	res = {}
	for track_id in possible_albums:
		options = possible_albums[track_id]
		if len(options) > 0:
			# pick the one with most occurences
			mostnum = max(options[albuminfo] for albuminfo in options)
			if mostnum >= MIN_NUM_TO_ASSIGN:
				bestpick = [albuminfo for albuminfo in options if options[albuminfo] == mostnum][0]
				*artists, title = bestpick
				res[track_id] = {"assigned": {
					"artists": artists,
					"albumtitle": title
				}}
				if len(artists) == 0:
					# for albums without artist, assume track artist
					res[track_id]["guess_artists"] = []
			else:
				res[track_id] = {"assigned": False, "reason": "Not enough data"}

		else:
			res[track_id] = {"assigned": False, "reason": "No scrobbles with album information found"}

	missing_artists = [track_id for track_id in res if "guess_artists" in res[track_id]]

	# we're pointlessly getting the albumartist names here even though the IDs would be enough
	# but it's better for function separation I guess
	jointable = sql.join(
		DB['trackartists'],
		DB['artists']
	)
	op = sql.select(
		DB['trackartists'].c.track_id,
		DB['artists']
	).select_from(jointable).where(
		DB['trackartists'].c.track_id.in_(missing_artists)
	)
	result = dbconn.execute(op).all()

	for row in result:
		res[row.track_id]["guess_artists"].append(row.name)

	return res
