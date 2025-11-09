"""
Database Cleanup and Deduplication Operations

This module handles database maintenance operations including orphan cleanup,
name normalization, and duplicate entity detection/merging. After migration
refactoring (Phase 7 - Task 7.2), cleanup operations are consolidated here
with proper ID cache invalidation.

## Cleanup Operations

The cleanup system maintains database integrity by:
1. Removing orphaned entities (tracks/artists/albums with no references)
2. Removing NULL associations in junction tables
3. Renormalizing entity names to ensure consistent lookups
4. Detecting and merging duplicate tracks with identical names and artists
5. Detecting and merging duplicate albums with identical names and artists

## Cache Invalidation (Requirement 2.1)

Operations that modify entity names or merge duplicates invalidate ID caches:

1. **renormalize_names()** - Calls reset_id_caches() after normalization
   - Name normalization changes affect ID resolution
   - Ensures subsequent lookups use updated normalized names

2. **merge_duplicate_tracks()** - Calls reset_id_caches() after merges
   - Merging duplicates changes which IDs resolve to which entities
   - Delegates to merge_tracks() which handles cache invalidation

3. **merge_duplicate_albums()** - Calls reset_id_caches() after merges
   - Merging duplicates changes which IDs resolve to which entities
   - Delegates to merge_albums() which handles cache invalidation

Note: clean_db() does NOT invalidate caches because it only removes orphaned
entities that are not referenced anywhere and thus not cached.

## Functions

### clean_db(dbconn=None)
Remove orphaned entities and invalid associations from database.

**Cleanup Strategy (order matters):**
1. NULL associations (albumartists, trackartists with NULL foreign keys)
2. Tracks with no scrobbles (including their trackartist entries)
3. Artists with no tracks AND no albums AND not associated
4. Tracks with no artists (including their scrobbles)
5. Albums with no tracks (including their albumartist entries)
6. Albumartist/trackartist entries with missing references

**Example:**
```python
clean_db()  # Clean entire database
```

**Why order matters:**
- Must delete junction table entries (trackartists, albumartists) before deleting entities
- Must delete scrobbles before deleting tracks
- Cascading deletes must happen in correct order to avoid foreign key violations

**Thread Safety:**
Uses SCROBBLE_LOCK to prevent concurrent modifications during cleanup.

**Side Effects:**
- Deletes orphaned tracks, artists, albums
- Deletes invalid trackartists and albumartists entries
- Logs all deletions
- Does NOT invalidate caches (only removes unreferenced entities)

### renormalize_names()
Fix any artist names that have incorrect name_normalized values.

**Name Normalization:**
Normalized names are used for duplicate detection and ID resolution.
Format: lowercase, stripped, collapsed spaces.
Example: "  The Beatles  " → "the beatles"

**Process:**
1. Select all artists from database
2. For each artist, check if name_normalized matches expected value
3. If mismatch, update name_normalized to correct value
4. Invalidate ID cache after all updates

**Decorators:**
- @runmonthly - Only runs once per month (not on every server start)
- @no_aux_mode - Does not run in auxiliary mode

**Example:**
```python
renormalize_names()  # Fix any normalization mismatches
```

**Thread Safety:**
Uses SCROBBLE_LOCK to prevent concurrent modifications during normalization.

**Side Effects:**
- Updates artists.name_normalized column
- Calls reset_id_caches() (Requirement 2.1)
- Logs all normalization fixes

### merge_duplicate_tracks(artist_id=None, dbconn=None)
Detect and merge duplicate tracks with identical names and artists.

**Duplicate Detection:**
Tracks are duplicates if they have:
1. The same set of artists (exactly)
2. The same normalized title

**Algorithm:**
1. Get all tracks for specified artist (or all tracks if artist_id=None)
2. Group tracks by their artist set
3. Within each artist set, group by normalized title
4. For each group with multiple tracks, merge into first track

**Example:**
```python
# Merge duplicates for specific artist
merge_duplicate_tracks(artist_id=42)

# Merge ALL duplicate tracks in database
merge_duplicate_tracks()
```

**Why artist_id parameter?**
After merge_artists(), we know exactly which tracks might have duplicates.
Specifying artist_id avoids checking the entire database.

**Side Effects:**
- Calls merge_tracks() for each duplicate set (which invalidates caches)
- Updates scrobbles table (via merge_tracks)
- Orphans source track records (cleaned by clean_db)

### merge_duplicate_albums(artist_id=None, dbconn=None)
Detect and merge duplicate albums with identical names and artists.

**Duplicate Detection:**
Albums are duplicates if they have:
1. The same set of artists (exactly)
2. The same normalized album title

**Algorithm:**
1. Get all albums for specified artist (or all albums if artist_id=None)
2. Group albums by their artist set
3. Within each artist set, group by normalized album title
4. For each group with multiple albums, merge into first album

**Example:**
```python
# Merge duplicates for specific artist
merge_duplicate_albums(artist_id=42)

# Merge ALL duplicate albums in database
merge_duplicate_albums()
```

**Why artist_id parameter?**
After merge_artists(), we know exactly which albums might have duplicates.
Specifying artist_id avoids checking the entire database.

**Side Effects:**
- Calls merge_albums() for each duplicate set (which invalidates caches)
- Updates tracks table (via merge_albums)
- Orphans source album records (cleaned by clean_db)

## Usage Pattern

Cleanup operations are typically triggered by:
- **clean_db()**: Called after any merge operation, or manually via admin interface
- **renormalize_names()**: Runs automatically once per month (via @runmonthly decorator)
- **merge_duplicate_tracks/albums()**: Called after merge_artists() to handle new duplicates

## Typical Merge Flow

When merging artists, duplicates are created and cleaned up in this order:

1. merge_artists(target=99, sources=[100, 101])
   - Updates trackartists/albumartists to use target artist
   - Calls merge_duplicate_tracks(artist_id=99)
   - Calls merge_duplicate_albums(artist_id=99)
   - Calls clean_db()
2. merge_duplicate_tracks() finds tracks with same name+artists, merges them
3. merge_duplicate_albums() finds albums with same name+artists, merges them
4. clean_db() removes orphaned source entities

## Related Modules

- `maintenance.merges`: merge_tracks, merge_artists, merge_albums
- `queries.entities`: get_track, get_album for name lookups
- `utils.helpers`: normalize_name for duplicate detection
- `core.ids`: reset_id_caches for cache invalidation
- `core.schema`: DB table definitions
- `core.connection`: connection_provider, engine, SCROBBLE_LOCK

## Migration Notes

**Phase 7 - Task 7.2**: Extracted from database/sqldb.py (lines 508-656)
- Added reset_id_caches() call after renormalize_names()
- merge_duplicate_tracks and merge_duplicate_albums delegate cache invalidation to merge_tracks/merge_albums
- Imports merge_tracks/merge_albums from maintenance.merges (extracted in Task 7.1)
- clean_db does NOT call reset_id_caches() (only removes unreferenced entities)
"""

import sqlalchemy as sql
from doreah.logging import log
from doreah.regular import runmonthly, runhourly

from ..core.connection import connection_provider, engine
from ..crud.scrobbles import SCROBBLE_LOCK
from ..core.schema import DB
from ..core.ids import reset_id_caches
from ..utils.helpers import normalize_name
from ..queries.relationships import get_track, get_album
from .. import no_aux_mode


@runhourly
@connection_provider
@no_aux_mode
def clean_db(dbconn=None):
	"""
	Remove orphaned entities and invalid associations from database.

	Removes entities in a specific order to avoid foreign key violations:
	1. NULL associations in junction tables
	2. Tracks without scrobbles (and their trackartist entries)
	3. Artists without tracks/albums/associations
	4. Tracks without artists (and their scrobbles)
	5. Albums without tracks (and their albumartist entries)
	6. Junction entries with missing entity references

	Args:
		dbconn: Database connection (optional, uses new connection if not provided)

	Side Effects:
		- Deletes orphaned tracks, artists, albums
		- Deletes invalid trackartists and albumartists entries
		- Logs all deletions
		- Uses SCROBBLE_LOCK for thread safety

	Note:
		Does NOT call reset_id_caches() because it only removes entities
		that are not referenced anywhere and thus not in ID cache.
	"""

	with SCROBBLE_LOCK:
		log(f"Database Cleanup...")

		to_delete = [
			# NULL associations
			"from albumartists where album_id is NULL",
			"from albumartists where artist_id is NULL",
			"from trackartists where track_id is NULL",
			"from trackartists where artist_id is NULL",
			# tracks with no scrobbles (trackartist entries first)
			"from trackartists where track_id in (select id from tracks where id not in (select track_id from scrobbles))",
			"from tracks where id not in (select track_id from scrobbles)",
			# artists with no tracks AND no albums
			"from artists where id not in (select artist_id from trackartists) \
				and id not in (select target_artist from associated_artists) \
				and id not in (select artist_id from albumartists)",
			# tracks with no artists (scrobbles first)
			"from scrobbles where track_id in (select id from tracks where id not in (select track_id from trackartists))",
			"from tracks where id not in (select track_id from trackartists)",
			# albums with no tracks (albumartist entries first)
			"from albumartists where album_id in (select id from albums where id not in (select album_id from tracks where album_id is not null))",
			"from albums where id not in (select album_id from tracks where album_id is not null)",
			# albumartist entries that are missing a reference
			"from albumartists where album_id not in (select album_id from tracks where album_id is not null)",
			"from albumartists where artist_id not in (select id from artists)",
			# trackartist entries that mare missing a reference
			"from trackartists where track_id not in (select id from tracks)",
			"from trackartists where artist_id not in (select id from artists)"
		]

		for d in to_delete:
			selection = dbconn.execute(sql.text(f"select * {d}"))
			for row in selection.all():
				log(f"Deleting {row}")
			deletion = dbconn.execute(sql.text(f"delete {d}"))

		log("Database Cleanup complete!")


@runmonthly
@no_aux_mode
def renormalize_names():
	"""
	Fix any artist names that have incorrect name_normalized values.

	Checks all artists in the database and updates name_normalized to match
	the expected normalized form of the name. Normalized names are used for
	duplicate detection and ID resolution.

	Name normalization format: lowercase, stripped, collapsed spaces.
	Example: "  The Beatles  " → "the beatles"

	Decorators:
		@runmonthly: Only runs once per month (not on every server start)
		@no_aux_mode: Does not run in auxiliary mode

	Side Effects:
		- Updates artists.name_normalized column for mismatched entries
		- Calls reset_id_caches() after all updates (Requirement 2.1)
		- Logs all normalization fixes
		- Uses SCROBBLE_LOCK for thread safety

	Cache Invalidation:
		Calls reset_id_caches() because name normalization affects ID resolution.
		Changed normalized names must trigger cache invalidation to ensure
		subsequent ID lookups use the updated values.
	"""

	with SCROBBLE_LOCK:
		with engine.begin() as conn:
			rows = conn.execute(DB['artists'].select()).all()

			for row in rows:
				id = row.id
				name = row.name
				norm_actual = row.name_normalized
				norm_target = normalize_name(name)
				if norm_actual != norm_target:
					log(f"{name} should be normalized to {norm_target}, but is instead {norm_actual}, fixing...")

					rows = conn.execute(DB['artists'].update().where(DB['artists'].c.id == id).values(name_normalized=norm_target))

			# Invalidate ID cache after normalization (Requirement 2.1)
			reset_id_caches()


@connection_provider
def merge_duplicate_tracks(artist_id=None, dbconn=None):
	"""
	Detect and merge duplicate tracks with identical names and artists.

	Groups tracks by their artist set, then by normalized title within each group.
	Merges all tracks with the same artists and title into the first track found.

	Args:
		artist_id: If specified, only check tracks by this artist (optimization)
		dbconn: Database connection (provided by decorator)

	Algorithm:
		1. Get all tracks for specified artist (or all tracks if artist_id=None)
		2. Group tracks by their artist set (exact match required)
		3. Within each artist set, group by normalized title
		4. For each group with 2+ tracks, merge into first track

	Example:
		# After merging "The Beatles" (ID 100) into "Beatles" (ID 99)
		merge_duplicate_tracks(artist_id=99)
		# This finds tracks like "Yesterday" that now have duplicate entries

	Side Effects:
		- Calls merge_tracks() for each duplicate set
		- merge_tracks() handles cache invalidation (calls reset_id_caches)
		- Updates scrobbles table (via merge_tracks)
		- Orphans source track records (cleaned by clean_db)

	Cache Invalidation:
		Delegates to merge_tracks() which calls reset_id_caches().
		This function does NOT directly call reset_id_caches().
	"""

	affected_track_conditions = []
	if artist_id:
		affected_track_conditions = [DB['trackartists'].c.artist_id == artist_id]

	rows = dbconn.execute(
		DB['trackartists'].select().where(
			*affected_track_conditions
		)
	)
	affected_tracks = [r.track_id for r in rows]

	track_artists = {}
	rows = dbconn.execute(
		DB['trackartists'].select().where(
			DB['trackartists'].c.track_id.in_(affected_tracks)
		)
	)

	for row in rows:
		track_artists.setdefault(row.track_id, []).append(row.artist_id)

	artist_combos = {}
	for track_id in track_artists:
		artist_combos.setdefault(tuple(sorted(track_artists[track_id])), []).append(track_id)

	for c in artist_combos:
		if len(artist_combos[c]) > 1:
			track_identifiers = {}
			for track_id in artist_combos[c]:
				track_identifiers.setdefault(normalize_name(get_track(track_id)['title']), []).append(track_id)
			for track in track_identifiers:
				if len(track_identifiers[track]) > 1:
					# Import here to avoid circular dependency
					from .merges import merge_tracks
					target, *src = track_identifiers[track]
					merge_tracks(target, src, dbconn=dbconn)


@connection_provider
def merge_duplicate_albums(artist_id=None, dbconn=None):
	"""
	Detect and merge duplicate albums with identical names and artists.

	Groups albums by their artist set, then by normalized album title within each group.
	Merges all albums with the same artists and title into the first album found.

	Args:
		artist_id: If specified, only check albums by this artist (optimization)
		dbconn: Database connection (provided by decorator)

	Algorithm:
		1. Get all albums for specified artist (or all albums if artist_id=None)
		2. Group albums by their artist set (exact match required)
		3. Within each artist set, group by normalized album title
		4. For each group with 2+ albums, merge into first album

	Example:
		# After merging "The Beatles" (ID 100) into "Beatles" (ID 99)
		merge_duplicate_albums(artist_id=99)
		# This finds albums like "Abbey Road" that now have duplicate entries

	Side Effects:
		- Calls merge_albums() for each duplicate set
		- merge_albums() handles cache invalidation (calls reset_id_caches)
		- Updates tracks table (via merge_albums)
		- Orphans source album records (cleaned by clean_db)

	Cache Invalidation:
		Delegates to merge_albums() which calls reset_id_caches().
		This function does NOT directly call reset_id_caches().
	"""

	affected_album_conditions = []
	if artist_id:
		affected_album_conditions = [DB['albumartists'].c.artist_id == artist_id]

	rows = dbconn.execute(
		DB['albumartists'].select().where(
			*affected_album_conditions
		)
	)
	affected_albums = [r.album_id for r in rows]

	album_artists = {}
	rows = dbconn.execute(
		DB['albumartists'].select().where(
			DB['albumartists'].c.album_id.in_(affected_albums)
		)
	)

	for row in rows:
		album_artists.setdefault(row.album_id, []).append(row.artist_id)

	artist_combos = {}
	for album_id in album_artists:
		artist_combos.setdefault(tuple(sorted(album_artists[album_id])), []).append(album_id)

	for c in artist_combos:
		if len(artist_combos[c]) > 1:
			album_identifiers = {}
			for album_id in artist_combos[c]:
				album_identifiers.setdefault(normalize_name(get_album(album_id)['albumtitle']), []).append(album_id)
			for album in album_identifiers:
				if len(album_identifiers[album]) > 1:
					# Import here to avoid circular dependency
					from .merges import merge_albums
					target, *src = album_identifiers[album]
					merge_albums(target, src, dbconn=dbconn)
