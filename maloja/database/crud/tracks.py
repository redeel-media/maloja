"""
Track CRUD Operations
=====================

This module contains Create, Read, Update, Delete (CRUD) operations for tracks
and track-album associations.

ARCHITECTURE:
  This is part of the CRUD layer in the database module hierarchy:
  - core/ (schema, connection, types, IDs, cache)
  - crud/ (scrobbles, tracks, artists, albums) ← YOU ARE HERE
  - queries/ (aggregation, charts, statistics)
  - charts/ (specialized chart operations)

ID CACHE INVALIDATION:
  When edit_track() changes track information (title, artist names), cached
  ID lookups may become stale. The wrapper layer (database/__init__.py) is
  responsible for calling reset_id_caches() from core.ids after edit_track()
  to clear these caches.

  Example flow:
    1. User edits track: "Song" by "Artist A" → "Song" by "Artist B"
    2. edit_track() updates database
    3. Wrapper calls reset_id_caches() (clears get_track_id cache)
    4. Wrapper calls dbcache.invalidate_entity_cache()
    5. Wrapper calls dbcache.invalidate_caches()
    6. Next get_track_id() call will see new track info

CACHE INVALIDATION:
  These functions are called directly by database/__init__.py wrapper functions.
  Cache invalidation is handled by the wrapper layer, NOT here. This allows:
  - Single responsibility: CRUD layer only handles database operations
  - Flexibility: Wrappers can batch operations before invalidating
  - Testability: CRUD functions can be tested without cache side effects

TRACK-ALBUM ASSOCIATIONS:
  Tracks can optionally belong to albums. Album associations are stored in
  tracks.album_id foreign key. Functions in this module manage:
  - add_tracks_to_albums(): Bulk assign tracks to albums
  - remove_album(): Detach tracks from albums (set album_id to NULL)

  Note: add_track_to_album() (singular) is in core/ids.py because it's part
  of the ID resolution process during scrobble insertion.

DEPENDENCIES:
  - core.schema: DB (table definitions)
  - core.connection: connection_provider decorator
  - core.types: TrackDict type definitions
  - core.ids: get_track_id, add_track_to_album (for ID resolution)
  - crud.converters: track_dict_to_db converter
  - exceptions: TrackExists

EXPORTED FUNCTIONS:
  - edit_track(track_id, trackupdatedict, dbconn=None) -> bool
      Update track information (title, artists, length)
  - add_tracks_to_albums(track_to_album_id_dict, replace=False, dbconn=None) -> bool
      Bulk assign tracks to albums
  - remove_album(*track_ids, dbconn=None) -> bool
      Detach tracks from albums (set album_id to NULL)

USAGE EXAMPLE:
  from maloja.database.crud import tracks

  # Edit track title and artists
  tracks.edit_track(track_id=123, trackupdatedict={
      'title': 'New Title',
      'artists': ['New Artist 1', 'New Artist 2']
  })
  # Wrapper will call reset_id_caches() after this

  # Bulk assign tracks to album
  tracks.add_tracks_to_albums({
      track_id_1: album_id_1,
      track_id_2: album_id_1,
      track_id_3: album_id_2
  }, replace=True)

  # Remove album association
  tracks.remove_album(track_id_1, track_id_2, track_id_3)

MIGRATION NOTES:
  This module was extracted from database/sqldb.py as part of Phase 4
  refactoring. Original location: sqldb.py lines 95-111 (add_tracks_to_albums,
  remove_album), lines 142-161 (edit_track).

SEE ALSO:
  - database/__init__.py: Wrapper functions that handle cache invalidation
  - core/ids.py: add_track_to_album() (singular) for scrobble insertion
  - crud/converters.py: track_dict_to_db() converter
  - docs/id_cache_invalidation.md: ID cache invalidation strategy
"""

from ..core.schema import DB
from ..core.connection import connection_provider
from ..core.types import TrackDict
from ..core.ids import get_track_id, add_track_to_album
from .converters import track_dict_to_db
from .. import exceptions as exc


# ============================================================================
# TRACK-ALBUM ASSOCIATION OPERATIONS
# ============================================================================

@connection_provider
def add_tracks_to_albums(track_to_album_id_dict: dict[int, int], replace=False, dbconn=None) -> bool:
	"""
	Bulk assign tracks to albums.

	This is a convenience wrapper around add_track_to_album() (from core.ids)
	that processes multiple track-album associations in a single call.

	Args:
		track_to_album_id_dict: Dict mapping track IDs to album IDs
		                        {track_id: album_id, ...}
		replace: If True, replace existing album association
		         If False, only set album if track has no album
		dbconn: Database connection (provided by decorator)

	Returns:
		True (always)

	Cache Invalidation:
		Caller is responsible for calling dbcache.invalidate_entity_cache()
		after this function returns

	Example:
		# Assign tracks 1, 2, 3 to album 10
		add_tracks_to_albums({
			1: 10,
			2: 10,
			3: 10
		}, replace=True)

		# Assign tracks to different albums
		add_tracks_to_albums({
			1: 10,
			2: 11,
			3: 12
		})
	"""

	for track_id in track_to_album_id_dict:
		add_track_to_album(track_id, track_to_album_id_dict[track_id], replace=replace, dbconn=dbconn)
	return True


@connection_provider
def remove_album(*track_ids: list[int], dbconn=None) -> bool:
	"""
	Detach tracks from their albums.

	Sets tracks.album_id to NULL for specified tracks. This is used when:
	- User wants to remove album association from tracks
	- Album is being deleted and tracks need to be orphaned

	Args:
		*track_ids: Variable number of track IDs to detach from albums
		dbconn: Database connection (provided by decorator)

	Returns:
		True (always, even if tracks had no albums)

	Cache Invalidation:
		Caller is responsible for calling dbcache.invalidate_entity_cache()
		after this function returns

	Note:
		This does NOT delete the tracks - only removes album association.
		Tracks will still exist and can be assigned to albums later.

	Example:
		# Remove album from single track
		remove_album(123)

		# Remove album from multiple tracks
		remove_album(123, 456, 789)
	"""

	DB['tracks'].update().where(
		DB['tracks'].c.track_id.in_(track_ids)
	).values(
		album_id=None
	)
	return True


# ============================================================================
# UPDATE OPERATIONS
# ============================================================================

@connection_provider
def edit_track(track_id: int, trackupdatedict: dict, dbconn=None) -> bool:
	"""
	Update an existing track's information.

	This is used for:
	- Correcting track metadata (title, artists, length)
	- Fixing misattributed tracks
	- Updating track information from external sources

	Args:
		track_id: ID of the track to edit
		trackupdatedict: Dictionary with fields to update (partial update supported)
		                 Valid fields: 'title', 'artists', 'length'
		dbconn: Database connection (provided by decorator)

	Returns:
		True (always)

	Raises:
		TrackExists: If the update would create a duplicate track
		             (same title + artists already exists with different ID)

	Cache Invalidation:
		Caller is responsible for:
		1. Calling reset_id_caches() from core.ids (clears cached ID lookups)
		2. Calling dbcache.invalidate_entity_cache() (clears entity metadata)
		3. Calling dbcache.invalidate_caches() (clears time-based caches)

	ID Cache Clearing:
		When track information changes (title, artist names), cached ID
		lookups become stale. Caller MUST call reset_id_caches() after
		edit_track() to clear these caches.

		Without clearing:
		- get_track_id({'title': 'Old Title', 'artists': ['Old Artist']})
		  would return cached ID even after title/artists changed
		- New scrobbles with updated info might create duplicate tracks

	Duplicate Detection:
		Before updating, checks if the new track info matches an existing
		track. If so, raises TrackExists exception. This prevents:
		- Accidentally merging two tracks via edit
		- Creating duplicate tracks in the database

		Example conflict:
		- Track 1: "Song" by "Artist A"
		- Track 2: "Song" by "Artist B"
		- Editing Track 1 to "Song" by "Artist B" → TrackExists exception

	Example:
		# Edit track title
		edit_track(123, {'title': 'New Title'})

		# Edit track artists (this changes ID resolution!)
		edit_track(123, {
			'artists': ['New Artist 1', 'New Artist 2']
		})
		# Caller must then call:
		#   reset_id_caches()        # Clear cached track IDs
		#   invalidate_entity_cache() # Clear track metadata cache
		#   invalidate_caches()       # Clear time-based caches

		# Edit multiple fields
		edit_track(123, {
			'title': 'New Title',
			'artists': ['New Artist'],
			'length': 240
		})
	"""

	# Import get_track from queries module
	# Phase 5: Moved to queries/relationships.py, no longer in sqldb
	from ..queries.relationships import get_track

	track = get_track(track_id, dbconn=dbconn)
	changedtrack: TrackDict = {**track, **trackupdatedict}

	dbentry = track_dict_to_db(trackupdatedict, dbconn=dbconn)
	dbentry = {k: v for k, v in dbentry.items() if v}

	existing_track_id = get_track_id(changedtrack, create_new=False, dbconn=dbconn)
	if existing_track_id not in (None, track_id):
		raise exc.TrackExists(changedtrack)

	op = DB['tracks'].update().where(
		DB['tracks'].c.id == track_id
	).values(
		**dbentry
	)
	result = dbconn.execute(op)
	return True
