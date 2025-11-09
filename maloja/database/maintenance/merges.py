"""
Entity Merge Operations with Cache Invalidation

This module handles merging of duplicate entities (tracks, artists, albums) in the database.
After migration refactoring (Phase 7 - Task 7.1), merge operations are consolidated here
with proper ID cache and general cache invalidation.

## Merge Operations

Entity merging consolidates multiple entity IDs into a single target entity by:
1. Updating foreign key references in related tables
2. Removing duplicate relationships created by the merge
3. Triggering duplicate detection and merging for affected entities
4. Cleaning up orphaned entities
5. Invalidating all caches to reflect changes

## Cache Invalidation (Requirement 2.1)

All merge operations invalidate caches after completion:

1. **ID Cache Reset** (`reset_id_caches()`)
   - Clears artist/track/album ID resolution caches
   - Called after every merge operation
   - Ensures subsequent ID lookups see merged entities

2. **General Cache Invalidation** (`dbcache.invalidate_caches()`)
   - Clears all LRU function caches (charts, queries, etc.)
   - Called after every merge operation
   - Ensures all cached data reflects merged state

## Functions

### merge_tracks(target_id, source_ids, dbconn=None) -> bool
Merge multiple track IDs into a single target track.

**Process:**
1. Update all scrobbles pointing to source tracks to point to target track
2. Clean up orphaned tracks
3. Invalidate ID cache and general caches

**Example:**
```python
# Merge track IDs 42, 43, 44 into track ID 10
merge_tracks(target_id=10, source_ids=[42, 43, 44])
```

**Side Effects:**
- All scrobbles of source tracks become scrobbles of target track
- Source track records remain but become orphaned (cleaned by clean_db)
- ID cache cleared
- All function caches cleared

### merge_artists(target_id, source_ids, dbconn=None) -> bool
Merge multiple artist IDs into a single target artist.

**Process:**
1. Find all tracks and albums that have any of the involved artists
2. Delete all trackartists/albumartists entries for involved artists
3. Re-add target artist for all affected tracks/albums
4. Merge duplicate tracks created by the merge (same name + artists)
5. Merge duplicate albums created by the merge (same name + artists)
6. Clean up orphaned artists
7. Invalidate ID cache and general caches

**Example:**
```python
# Merge artist IDs 100, 101 into artist ID 99
merge_artists(target_id=99, source_ids=[100, 101])
```

**Why delete and re-add?**
Some tracks may already have multiple of the to-be-merged artists.
Example: Track has both "Beatles" (ID 99) and "The Beatles" (ID 100).
After merge, we only want one entry with target artist ID 99.

**Side Effects:**
- All tracks/albums by source artists become tracks/albums of target artist
- Duplicate tracks/albums are automatically merged
- Source artist records remain but become orphaned (cleaned by clean_db)
- ID cache cleared
- All function caches cleared

### merge_albums(target_id, source_ids, dbconn=None) -> bool
Merge multiple album IDs into a single target album.

**Process:**
1. Update all tracks pointing to source albums to point to target album
2. Clean up orphaned albums
3. Invalidate ID cache and general caches

**Example:**
```python
# Merge album IDs 200, 201 into album ID 199
merge_albums(target_id=199, source_ids=[200, 201])
```

**Side Effects:**
- All tracks from source albums become tracks of target album
- Source album records remain but become orphaned (cleaned by clean_db)
- ID cache cleared
- All function caches cleared

## Usage Pattern

Merge operations are typically triggered by:
- Manual user consolidation (via web UI or API)
- Automated duplicate detection (merge_duplicate_tracks/albums in cleanup.py)
- Data import normalization

All merge operations require a transaction context and will be committed
by the @connection_provider decorator.

## Related Modules

- `core.ids`: ID cache management (reset_id_caches)
- `core.schema`: Database table definitions (DB)
- `maintenance.cleanup`: Duplicate detection (merge_duplicate_tracks/albums, clean_db)
- `dbcache`: General cache invalidation (invalidate_caches)

## Migration Notes

**Phase 7 - Task 7.1**: Extracted from database/sqldb.py (lines 223-316)
- Added @connection_provider to merge_tracks (was missing)
- Added reset_id_caches() call after each merge operation
- Added dbcache.invalidate_caches() call after each merge operation
- Imports clean_db from parent module (will be extracted in Task 7.2)
- Imports merge_duplicate_tracks/albums from parent module (will be extracted in Task 7.2)
"""

import sqlalchemy as sql

from ..core.connection import connection_provider
from ..core.schema import DB
from ..core.ids import reset_id_caches
from .. import dbcache


@connection_provider
def merge_tracks(target_id: int, source_ids: list[int], dbconn=None) -> bool:
	"""
	Merge multiple track IDs into a single target track.

	Updates all scrobbles pointing to source tracks to point to target track,
	then cleans up orphaned entities and invalidates caches.

	Args:
		target_id: The track ID to merge into (this track will remain)
		source_ids: List of track IDs to merge (these become orphaned)
		dbconn: Database connection (provided by decorator)

	Returns:
		bool: True on success

	Side Effects:
		- Updates scrobbles table (track_id foreign key)
		- Calls clean_db() to remove orphaned tracks
		- Calls reset_id_caches() to clear ID resolution cache
		- Calls dbcache.invalidate_caches() to clear all function caches
	"""
	# Import here to avoid circular dependency (clean_db extracted in Task 7.2)
	from .cleanup import clean_db

	op = DB['scrobbles'].update().where(
		DB['scrobbles'].c.track_id.in_(source_ids)
	).values(
		track_id=target_id
	)
	result = dbconn.execute(op)

	# Clean up orphaned tracks
	clean_db(dbconn=dbconn)

	# Invalidate ID cache (Requirement 2.1)
	reset_id_caches()

	# Invalidate all function caches (Requirement 2.1)
	dbcache.invalidate_caches()

	return True


@connection_provider
def merge_artists(target_id: int, source_ids: list[int], dbconn=None) -> bool:
	"""
	Merge multiple artist IDs into a single target artist.

	Updates trackartists and albumartists tables by removing all involved artists
	and re-adding only the target artist. This handles cases where tracks/albums
	already have multiple of the to-be-merged artists. Then triggers duplicate
	detection for tracks and albums that may have become duplicates.

	Args:
		target_id: The artist ID to merge into (this artist will remain)
		source_ids: List of artist IDs to merge (these become orphaned)
		dbconn: Database connection (provided by decorator)

	Returns:
		bool: True on success

	Side Effects:
		- Updates trackartists table (removes and re-adds entries)
		- Updates albumartists table (removes and re-adds entries)
		- Calls merge_duplicate_tracks() to handle tracks that became duplicates
		- Calls merge_duplicate_albums() to handle albums that became duplicates
		- Calls clean_db() to remove orphaned artists
		- Calls reset_id_caches() to clear ID resolution cache
		- Calls dbcache.invalidate_caches() to clear all function caches
	"""
	# Import here to avoid circular dependency (extracted in Task 7.2)
	from .cleanup import clean_db, merge_duplicate_tracks, merge_duplicate_albums

	# Some tracks could already have multiple of the to-be-merged artists

	# Find literally all trackartist entries that have any of the artists involved
	op = DB['trackartists'].select().where(
		DB['trackartists'].c.artist_id.in_(source_ids + [target_id])
	)
	result = dbconn.execute(op)

	track_ids = set(row.track_id for row in result)

	# Now delete them all
	op = DB['trackartists'].delete().where(
		DB['trackartists'].c.artist_id.in_(source_ids + [target_id]),
	)
	result = dbconn.execute(op)

	# Now add back the real new artist
	op = DB['trackartists'].insert().values([
		{'track_id': track_id, 'artist_id': target_id}
		for track_id in track_ids
	])
	result = dbconn.execute(op)

	# Same for albums
	op = DB['albumartists'].select().where(
		DB['albumartists'].c.artist_id.in_(source_ids + [target_id])
	)
	result = dbconn.execute(op)

	album_ids = set(row.album_id for row in result)

	op = DB['albumartists'].delete().where(
		DB['albumartists'].c.artist_id.in_(source_ids + [target_id]),
	)
	result = dbconn.execute(op)

	op = DB['albumartists'].insert().values([
		{'album_id': album_id, 'artist_id': target_id}
		for album_id in album_ids
	])
	result = dbconn.execute(op)

	# This could have created duplicate tracks and albums
	merge_duplicate_tracks(artist_id=target_id, dbconn=dbconn)
	merge_duplicate_albums(artist_id=target_id, dbconn=dbconn)

	# Clean up orphaned artists
	clean_db(dbconn=dbconn)

	# Invalidate ID cache (Requirement 2.1)
	reset_id_caches()

	# Invalidate all function caches (Requirement 2.1)
	dbcache.invalidate_caches()

	return True


@connection_provider
def merge_albums(target_id: int, source_ids: list[int], dbconn=None) -> bool:
	"""
	Merge multiple album IDs into a single target album.

	Updates all tracks pointing to source albums to point to target album,
	then cleans up orphaned entities and invalidates caches.

	Args:
		target_id: The album ID to merge into (this album will remain)
		source_ids: List of album IDs to merge (these become orphaned)
		dbconn: Database connection (provided by decorator)

	Returns:
		bool: True on success

	Side Effects:
		- Updates tracks table (album_id foreign key)
		- Calls clean_db() to remove orphaned albums
		- Calls reset_id_caches() to clear ID resolution cache
		- Calls dbcache.invalidate_caches() to clear all function caches
	"""
	# Import here to avoid circular dependency (clean_db extracted in Task 7.2)
	from .cleanup import clean_db

	op = DB['tracks'].update().where(
		DB['tracks'].c.album_id.in_(source_ids)
	).values(
		album_id=target_id
	)
	result = dbconn.execute(op)

	# Clean up orphaned albums
	clean_db(dbconn=dbconn)

	# Invalidate ID cache (Requirement 2.1)
	reset_id_caches()

	# Invalidate all function caches (Requirement 2.1)
	dbcache.invalidate_caches()

	return True
