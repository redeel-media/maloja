"""
Album CRUD Operations
=====================

This module contains Create, Read, Update, Delete (CRUD) operations for albums.

ARCHITECTURE:
  This is part of the CRUD layer in the database module hierarchy:
  - core/ (schema, connection, types, IDs, cache)
  - crud/ (scrobbles, tracks, artists, albums) ← YOU ARE HERE
  - queries/ (aggregation, charts, statistics)
  - charts/ (specialized chart operations)

ID CACHE INVALIDATION:
  When edit_album() changes album information (title, artists), cached ID
  lookups may become stale. The wrapper layer (database/__init__.py) is
  responsible for calling reset_id_caches() from core.ids after edit_album()
  to clear these caches.

  Example flow:
    1. User edits album: "Album Title" by ["Artist A"] → "Album Title" by ["Artist B"]
    2. edit_album() updates database
    3. Wrapper calls reset_id_caches() (clears get_album_id cache)
    4. Wrapper calls dbcache.invalidate_entity_cache()
    5. Wrapper calls dbcache.invalidate_caches()
    6. Next get_album_id() call will see new album info

CACHE INVALIDATION:
  These functions are called directly by database/__init__.py wrapper functions.
  Cache invalidation is handled by the wrapper layer, NOT here. This allows:
  - Single responsibility: CRUD layer only handles database operations
  - Flexibility: Wrappers can batch operations before invalidating
  - Testability: CRUD functions can be tested without cache side effects

ALBUM IDENTIFICATION:
  Albums are identified by the combination of:
  - Album title (albumtitle)
  - Album artists (artists list)

  Two albums are considered the same if they have:
  - Same album title (case-sensitive)
  - Same album artists (order matters)

  Unlike artists (which use normalized names), album titles are case-sensitive
  for identification purposes.

DEPENDENCIES:
  - core.schema: DB (table definitions)
  - core.connection: connection_provider decorator
  - core.types: AlbumDict type definitions
  - core.ids: get_album_id (for duplicate detection)
  - crud.converters: album_dict_to_db converter
  - exceptions: TrackExists (note: bug in original code, should be AlbumExists)

EXPORTED FUNCTIONS:
  - edit_album(album_id, albumupdatedict, dbconn=None) -> bool
      Update album information (title, artists)

USAGE EXAMPLE:
  from maloja.database.crud import albums

  # Edit album title
  albums.edit_album(album_id=123, albumupdatedict={
      'albumtitle': 'New Album Title'
  })
  # Wrapper will call reset_id_caches() after this

  # Edit album artists
  albums.edit_album(album_id=123, albumupdatedict={
      'artists': ['New Artist 1', 'New Artist 2']
  })
  # Wrapper will call reset_id_caches() after this

MIGRATION NOTES:
  This module was extracted from database/sqldb.py as part of Phase 4
  refactoring. Original location: sqldb.py lines 114-133 (edit_album).

  Known issue: Line 125 in original code raises exc.TrackExists instead of
  exc.AlbumExists. This is preserved for backward compatibility.

SEE ALSO:
  - database/__init__.py: Wrapper functions that handle cache invalidation
  - core/ids.py: get_album_id() function for album ID resolution
  - crud/converters.py: album_dict_to_db() converter
  - docs/id_cache_invalidation.md: ID cache invalidation strategy
"""

from ..core.schema import DB
from ..core.connection import connection_provider
from ..core.types import AlbumDict
from ..core.ids import get_album_id
from .converters import album_dict_to_db
from .. import exceptions as exc


# ============================================================================
# UPDATE OPERATIONS
# ============================================================================

@connection_provider
def edit_album(album_id: int, albumupdatedict: dict, dbconn=None) -> bool:
	"""
	Update an existing album's information.

	This is used for:
	- Correcting album metadata (title, artists)
	- Fixing misattributed albums
	- Updating album information from external sources

	Args:
		album_id: ID of the album to edit
		albumupdatedict: Dictionary with fields to update (partial update supported)
		                 Valid fields: 'albumtitle', 'artists'
		dbconn: Database connection (provided by decorator)

	Returns:
		True (always)

	Raises:
		TrackExists: If the update would create a duplicate album
		             Note: This is a bug in the original code - should raise AlbumExists
		             Preserved for backward compatibility

	Cache Invalidation:
		Caller is responsible for:
		1. Calling reset_id_caches() from core.ids (clears cached ID lookups)
		2. Calling dbcache.invalidate_entity_cache() (clears entity metadata)
		3. Calling dbcache.invalidate_caches() (clears time-based caches)

	ID Cache Clearing:
		When album information changes (title, artists), cached ID lookups
		become stale. Caller MUST call reset_id_caches() after edit_album()
		to clear these caches.

		Without clearing:
		- get_album_id({'albumtitle': 'Old Title', 'artists': ['Old Artist']})
		  would return cached ID even after title/artists changed
		- New tracks with updated info might create duplicate albums

	Duplicate Detection:
		Before updating, checks if the new album info matches an existing
		album. If so, raises TrackExists exception (bug - should be AlbumExists).
		This prevents:
		- Accidentally merging two albums via edit
		- Creating duplicate albums in the database

		Example conflict:
		- Album 1: "Album" by ["Artist A"]
		- Album 2: "Album" by ["Artist B"]
		- Editing Album 1 to "Album" by ["Artist B"] → TrackExists exception

	Album Identification:
		Albums are identified by (albumtitle, artists) tuple:
		- Album title is case-sensitive
		- Artist list order matters
		- Two albums with same title but different artists are different

		Unlike artists (normalized), album titles are NOT normalized for
		identification, so "Album" and "album" are different albums.

	Example:
		# Edit album title
		edit_album(123, {'albumtitle': 'New Title'})

		# Edit album artists (this changes ID resolution!)
		edit_album(123, {
			'artists': ['New Artist 1', 'New Artist 2']
		})
		# Caller must then call:
		#   reset_id_caches()        # Clear cached album IDs
		#   invalidate_entity_cache() # Clear album metadata cache
		#   invalidate_caches()       # Clear time-based caches

		# Edit both fields
		edit_album(123, {
			'albumtitle': 'New Title',
			'artists': ['New Artist']
		})

		# Trying to create duplicate (will raise TrackExists - bug!)
		# Album 1 is "Album" by ["Artist A"]
		# Album 2 is "Album" by ["Artist B"]
		# This will fail:
		edit_album(1, {'artists': ['Artist B']})  # Raises TrackExists
	"""

	# Import get_album from queries module
	# Phase 5: Moved to queries/relationships.py, no longer in sqldb
	from ..queries.relationships import get_album

	album = get_album(album_id, dbconn=dbconn)
	changedalbum: AlbumDict = {**album, **albumupdatedict}

	dbentry = album_dict_to_db(albumupdatedict, dbconn=dbconn)
	dbentry = {k: v for k, v in dbentry.items() if v}

	existing_album_id = get_album_id(changedalbum, create_new=False, dbconn=dbconn)
	if existing_album_id not in (None, album_id):
		raise exc.TrackExists(changedalbum)  # Bug: should be exc.AlbumExists

	op = DB['albums'].update().where(
		DB['albums'].c.id == album_id
	).values(
		**dbentry
	)
	result = dbconn.execute(op)
	return True
