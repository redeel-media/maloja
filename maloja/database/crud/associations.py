"""
Association CRUD Operations
============================

This module contains operations for managing many-to-many relationships between
entities (artists ↔ tracks, artists ↔ albums).

ARCHITECTURE:
  This is part of the CRUD layer in the database module hierarchy:
  - core/ (schema, connection, types, IDs, cache)
  - crud/ (scrobbles, tracks, artists, albums, associations) ← YOU ARE HERE
  - queries/ (aggregation, charts, statistics)
  - charts/ (specialized chart operations)

MANY-TO-MANY RELATIONSHIPS:
  The database uses junction tables to manage many-to-many relationships:

  1. trackartists: Links tracks to artists
     - track_id (foreign key to tracks)
     - artist_id (foreign key to artists)
     - A track can have multiple artists
     - An artist can have multiple tracks

  2. albumartists: Links albums to artists
     - album_id (foreign key to albums)
     - artist_id (foreign key to artists)
     - An album can have multiple artists
     - An artist can have multiple albums

DUPLICATE DETECTION:
  After modifying artist associations, duplicates may be created:

  Example (tracks):
    - Track A: "Song" by ["Artist 1", "Artist 2"]
    - Track B: "Song" by ["Artist 1"]
    - After adding "Artist 2" to Track B → duplicate of Track A

  Functions in this module call merge_duplicate_tracks() and
  merge_duplicate_albums() after association changes to detect and merge
  duplicates automatically.

CACHE INVALIDATION:
  These functions are called directly by database/__init__.py wrapper functions.
  Cache invalidation is handled by the wrapper layer, NOT here. This allows:
  - Single responsibility: CRUD layer only handles database operations
  - Flexibility: Wrappers can batch operations before invalidating
  - Testability: CRUD functions can be tested without cache side effects

ARTIST REMOVAL SAFETY:
  remove_artists_from_tracks() has a safety check to prevent creating
  tracks with zero artists (which would be invalid). It only removes artists
  from tracks that have at least one OTHER artist.

  remove_artists_from_albums() has NO such safety check - albums are allowed
  to have zero artists (compilation albums, various artists albums, etc.).

DEPENDENCIES:
  - core.schema: DB (table definitions)
  - core.connection: connection_provider decorator
  - sqlalchemy: SQL operations
  - sqldb: merge_duplicate_tracks(), merge_duplicate_albums()

EXPORTED FUNCTIONS:
  - add_artists_to_tracks(track_ids, artist_ids, dbconn=None) -> bool
      Add artists to tracks (many-to-many bulk operation)
  - remove_artists_from_tracks(track_ids, artist_ids, dbconn=None) -> bool
      Remove artists from tracks (with safety check for zero artists)
  - add_artists_to_albums(album_ids, artist_ids, dbconn=None) -> bool
      Add artists to albums (many-to-many bulk operation)
  - remove_artists_from_albums(album_ids, artist_ids, dbconn=None) -> bool
      Remove artists from albums (no safety check - albums can have zero artists)

USAGE EXAMPLE:
  from maloja.database.crud import associations

  # Add artists to tracks
  associations.add_artists_to_tracks(
      track_ids=[1, 2, 3],
      artist_ids=[10, 11]
  )
  # This adds Artist 10 and Artist 11 to Tracks 1, 2, and 3
  # Then merges any duplicate tracks created

  # Remove artists from tracks
  associations.remove_artists_from_tracks(
      track_ids=[1, 2],
      artist_ids=[10]
  )
  # This removes Artist 10 from Tracks 1 and 2
  # But only if they have at least one other artist

  # Add artists to albums
  associations.add_artists_to_albums(
      album_ids=[5],
      artist_ids=[20, 21]
  )

  # Remove artists from albums
  associations.remove_artists_from_albums(
      album_ids=[5],
      artist_ids=[20]
  )

MIGRATION NOTES:
  This module was extracted from database/sqldb.py as part of Phase 4
  refactoring. Original location: sqldb.py lines 121-192 (association
  management functions).

SEE ALSO:
  - database/__init__.py: Wrapper functions that handle cache invalidation
  - database/sqldb.py: merge_duplicate_tracks(), merge_duplicate_albums()
  - core/schema.py: DB table definitions (trackartists, albumartists)
  - docs/many_to_many_relationships.md: Relationship management strategy
"""

import sqlalchemy as sql

from ..core.schema import DB
from ..core.connection import connection_provider


# ============================================================================
# TRACK-ARTIST ASSOCIATIONS
# ============================================================================

@connection_provider
def add_artists_to_tracks(track_ids: list[int], artist_ids: list[int], dbconn=None) -> bool:
	"""
	Add artists to tracks (bulk many-to-many operation).

	This creates associations in the trackartists junction table. Each
	(track_id, artist_id) pair creates one association record.

	Args:
		track_ids: List of track IDs to add artists to
		artist_ids: List of artist IDs to add
		dbconn: Database connection (provided by decorator)

	Returns:
		True (always)

	Side Effects:
		Calls merge_duplicate_tracks() to detect and merge duplicate tracks
		created by the new associations. For example:
		- Track A: "Song" by ["Artist 1", "Artist 2"]
		- Track B: "Song" by ["Artist 1"]
		- After adding "Artist 2" to Track B → duplicate of Track A → merged

	Cache Invalidation:
		Caller is responsible for calling dbcache.invalidate_entity_cache()
		after this function returns

	Example:
		# Add Artist 10 and Artist 11 to Tracks 1, 2, and 3
		add_artists_to_tracks(
			track_ids=[1, 2, 3],
			artist_ids=[10, 11]
		)
		# This creates 6 associations:
		# (1, 10), (1, 11), (2, 10), (2, 11), (3, 10), (3, 11)
	"""

	# Import merge function here to avoid circular import
	# (sqldb imports from crud, so we can't import sqldb at module level)
	from ..sqldb import merge_duplicate_tracks

	op = DB['trackartists'].insert().values([
		{'track_id': track_id, 'artist_id': artist_id}
		for track_id in track_ids for artist_id in artist_ids
	])

	result = dbconn.execute(op)
	# the resulting tracks could now be duplicates of existing ones
	# this also takes care of clean_db
	merge_duplicate_tracks(dbconn=dbconn)
	return True


@connection_provider
def remove_artists_from_tracks(track_ids: list[int], artist_ids: list[int], dbconn=None) -> bool:
	"""
	Remove artists from tracks (bulk many-to-many operation).

	This removes associations from the trackartists junction table, but
	ONLY for tracks that have at least one other artist. This safety check
	prevents creating invalid tracks with zero artists.

	Args:
		track_ids: List of track IDs to remove artists from
		artist_ids: List of artist IDs to remove
		dbconn: Database connection (provided by decorator)

	Returns:
		True (always)

	Side Effects:
		Calls merge_duplicate_tracks() to detect and merge duplicate tracks
		after removal

	Safety Check:
		Only removes artists from tracks that have at least one OTHER artist.
		This prevents creating tracks with zero artists (which would be invalid).

		Example:
		- Track 1: "Song" by ["Artist A", "Artist B"]
		- Track 2: "Song" by ["Artist B"]
		- remove_artists_from_tracks([1, 2], [artist_B_id])
		  → Only removes from Track 1 (which still has Artist A)
		  → Does NOT remove from Track 2 (would leave it with zero artists)

	Cache Invalidation:
		Caller is responsible for calling dbcache.invalidate_entity_cache()
		after this function returns

	Example:
		# Remove Artist 10 from Tracks 1, 2, and 3
		# (only where they have other artists)
		remove_artists_from_tracks(
			track_ids=[1, 2, 3],
			artist_ids=[10]
		)
	"""

	# Import merge function here to avoid circular import
	# (sqldb imports from crud, so we can't import sqldb at module level)
	from ..sqldb import merge_duplicate_tracks

	# only tracks that have at least one other artist
	subquery = DB['trackartists'].select().where(
		~DB['trackartists'].c.artist_id.in_(artist_ids)
	).with_only_columns(
		DB['trackartists'].c.track_id
	).distinct().alias('sub')

	op = DB['trackartists'].delete().where(
		sql.and_(
			DB['trackartists'].c.track_id.in_(track_ids),
			DB['trackartists'].c.artist_id.in_(artist_ids),
			DB['trackartists'].c.track_id.in_(subquery.select())
		)
	)

	result = dbconn.execute(op)
	# the resulting tracks could now be duplicates of existing ones
	# this also takes care of clean_db
	merge_duplicate_tracks(dbconn=dbconn)
	return True


# ============================================================================
# ALBUM-ARTIST ASSOCIATIONS
# ============================================================================

@connection_provider
def add_artists_to_albums(album_ids: list[int], artist_ids: list[int], dbconn=None) -> bool:
	"""
	Add artists to albums (bulk many-to-many operation).

	This creates associations in the albumartists junction table. Each
	(album_id, artist_id) pair creates one association record.

	Args:
		album_ids: List of album IDs to add artists to
		artist_ids: List of artist IDs to add
		dbconn: Database connection (provided by decorator)

	Returns:
		True (always)

	Side Effects:
		Calls merge_duplicate_albums() to detect and merge duplicate albums
		created by the new associations. For example:
		- Album A: "Album" by ["Artist 1", "Artist 2"]
		- Album B: "Album" by ["Artist 1"]
		- After adding "Artist 2" to Album B → duplicate of Album A → merged

	Cache Invalidation:
		Caller is responsible for calling dbcache.invalidate_entity_cache()
		after this function returns

	Example:
		# Add Artist 20 and Artist 21 to Albums 5 and 6
		add_artists_to_albums(
			album_ids=[5, 6],
			artist_ids=[20, 21]
		)
		# This creates 4 associations:
		# (5, 20), (5, 21), (6, 20), (6, 21)
	"""

	# Import merge function here to avoid circular import
	# (sqldb imports from crud, so we can't import sqldb at module level)
	from ..sqldb import merge_duplicate_albums

	op = DB['albumartists'].insert().values([
		{'album_id':album_id,'artist_id':artist_id}
		for album_id in album_ids for artist_id in artist_ids
	])

	result = dbconn.execute(op)
	# the resulting albums could now be duplicates of existing ones
	# this also takes care of clean_db
	merge_duplicate_albums(dbconn=dbconn)
	return True


@connection_provider
def remove_artists_from_albums(album_ids: list[int], artist_ids: list[int], dbconn=None) -> bool:
	"""
	Remove artists from albums (bulk many-to-many operation).

	This removes associations from the albumartists junction table. Unlike
	remove_artists_from_tracks(), there is NO safety check here - albums
	are allowed to have zero artists (compilation albums, various artists, etc.).

	Args:
		album_ids: List of album IDs to remove artists from
		artist_ids: List of artist IDs to remove
		dbconn: Database connection (provided by decorator)

	Returns:
		True (always)

	Side Effects:
		Calls merge_duplicate_albums() to detect and merge duplicate albums
		after removal

	No Safety Check:
		Albums can have zero artists, so this function removes artists even
		if it leaves the album with no artists. This is intentional for:
		- Compilation albums (various artists)
		- Soundtracks
		- Unknown artist albums

	Cache Invalidation:
		Caller is responsible for calling dbcache.invalidate_entity_cache()
		after this function returns

	Example:
		# Remove Artist 20 from Albums 5 and 6
		remove_artists_from_albums(
			album_ids=[5, 6],
			artist_ids=[20]
		)
		# This removes even if it leaves albums with zero artists
	"""

	# Import merge function here to avoid circular import
	# (sqldb imports from crud, so we can't import sqldb at module level)
	from ..sqldb import merge_duplicate_albums

	# no check here, albums are allowed to have zero artists

	op = DB['albumartists'].delete().where(
		sql.and_(
			DB['albumartists'].c.album_id.in_(album_ids),
			DB['albumartists'].c.artist_id.in_(artist_ids)
		)
	)

	result = dbconn.execute(op)
	# the resulting albums could now be duplicates of existing ones
	# this also takes care of clean_db
	merge_duplicate_albums(dbconn=dbconn)
	return True
