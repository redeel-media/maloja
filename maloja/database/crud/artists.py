"""
Artist CRUD Operations
======================

This module contains Create, Read, Update, Delete (CRUD) operations for artists.

ARCHITECTURE:
  This is part of the CRUD layer in the database module hierarchy:
  - core/ (schema, connection, types, IDs, cache)
  - crud/ (scrobbles, tracks, artists, albums) ← YOU ARE HERE
  - queries/ (aggregation, charts, statistics)
  - charts/ (specialized chart operations)

ID CACHE INVALIDATION:
  When edit_artist() changes artist information (name), cached ID lookups may
  become stale because artist names are normalized for lookup. The wrapper layer
  (database/__init__.py) is responsible for calling reset_id_caches() from
  core.ids after edit_artist() to clear these caches.

  Example flow:
    1. User edits artist: "Artist Old Name" → "Artist New Name"
    2. edit_artist() updates database with normalized name
    3. Wrapper calls reset_id_caches() (clears get_artist_id cache)
    4. Wrapper calls dbcache.invalidate_entity_cache()
    5. Wrapper calls dbcache.invalidate_caches()
    6. Next get_artist_id() call will see new artist info

  Name normalization is critical:
    - Artist names are normalized (lowercase, stripped) for lookups
    - get_artist_id uses normalized names to find existing artists
    - Changing artist name changes normalized form
    - Cached lookups with old normalized name would fail

CACHE INVALIDATION:
  These functions are called directly by database/__init__.py wrapper functions.
  Cache invalidation is handled by the wrapper layer, NOT here. This allows:
  - Single responsibility: CRUD layer only handles database operations
  - Flexibility: Wrappers can batch operations before invalidating
  - Testability: CRUD functions can be tested without cache side effects

ARTIST NAME NORMALIZATION:
  Artists use normalized names for duplicate detection and lookups.
  See utils.helpers.normalize_name() for normalization logic:
  - Convert to lowercase
  - Strip whitespace
  - Collapse multiple spaces to single space
  - etc.

  This means "The Beatles", "the beatles", and "THE BEATLES" are all
  considered the same artist.

DEPENDENCIES:
  - core.schema: DB (table definitions)
  - core.connection: connection_provider decorator
  - core.ids: get_artist_id (for duplicate detection)
  - crud.converters: artist_dict_to_db converter
  - exceptions: ArtistExists

EXPORTED FUNCTIONS:
  - edit_artist(artist_id, artistupdatedict, dbconn=None) -> bool
      Update artist information (name)

USAGE EXAMPLE:
  from maloja.database.crud import artists

  # Edit artist name
  artists.edit_artist(artist_id=123, artistupdatedict='New Artist Name')
  # Wrapper will call reset_id_caches() after this

MIGRATION NOTES:
  This module was extracted from database/sqldb.py as part of Phase 4
  refactoring. Original location: sqldb.py lines 109-128 (edit_artist).

SEE ALSO:
  - database/__init__.py: Wrapper functions that handle cache invalidation
  - core/ids.py: get_artist_id() function with normalized name caching
  - utils/helpers.py: normalize_name() function for artist name normalization
  - docs/id_cache_invalidation.md: ID cache invalidation strategy
"""

from ..core.schema import DB
from ..core.connection import connection_provider
from ..core.ids import get_artist_id
from .converters import artist_dict_to_db
from .. import exceptions as exc


# ============================================================================
# UPDATE OPERATIONS
# ============================================================================

@connection_provider
def edit_artist(artist_id: int, artistupdatedict: str, dbconn=None) -> bool:
	"""
	Update an existing artist's information.

	This is used for:
	- Correcting artist names (spelling, capitalization)
	- Merging duplicate artist entries (via name changes)
	- Updating artist information from external sources

	Args:
		artist_id: ID of the artist to edit
		artistupdatedict: New artist name (string)
		                  Note: Despite the name "dict", this is actually a string
		                        (legacy naming from original implementation)
		dbconn: Database connection (provided by decorator)

	Returns:
		True (always)

	Raises:
		ArtistExists: If the update would create a duplicate artist
		              (same normalized name already exists with different ID)

	Cache Invalidation:
		Caller is responsible for:
		1. Calling reset_id_caches() from core.ids (clears cached ID lookups)
		2. Calling dbcache.invalidate_entity_cache() (clears entity metadata)
		3. Calling dbcache.invalidate_caches() (clears time-based caches)

	ID Cache Clearing:
		When artist name changes, normalized name changes, which affects ID
		lookups. Caller MUST call reset_id_caches() after edit_artist() to
		clear cached ID lookups.

		Without clearing:
		- get_artist_id('Old Name') would return cached ID
		- New scrobbles with updated name might create duplicate artists
		- Name normalization cache would be stale

	Duplicate Detection:
		Before updating, checks if the new artist name (normalized) matches
		an existing artist. If so, raises ArtistExists exception. This prevents:
		- Accidentally merging two artists via edit
		- Creating duplicate artists in the database

		Example conflict:
		- Artist 1: "The Beatles"
		- Artist 2: "Beatles"
		- Editing Artist 1 to "Beatles" → ArtistExists exception
		  (because "Beatles" normalized = "beatles" already exists)

	Name Normalization:
		Artist names are normalized for lookups (lowercase, stripped, etc.).
		See utils.helpers.normalize_name() for details. This means:
		- "The Beatles" and "the beatles" are considered duplicates
		- "Led Zeppelin" and "LED ZEPPELIN" are considered duplicates

	Example:
		# Edit artist name
		edit_artist(123, 'New Artist Name')

		# Caller must then call:
		#   reset_id_caches()        # Clear cached artist IDs
		#   invalidate_entity_cache() # Clear artist metadata cache
		#   invalidate_caches()       # Clear time-based caches

		# Trying to create duplicate (will raise ArtistExists)
		# Artist 1 is "The Beatles"
		# Artist 2 is "Beatles, The"
		# This will fail:
		edit_artist(1, 'Beatles, The')  # Raises ArtistExists
	"""

	# Import get_artist from queries module
	# Phase 5: Moved to queries/relationships.py, no longer in sqldb
	from ..queries.relationships import get_artist

	artist = get_artist(artist_id)
	changedartist = artistupdatedict  # well (legacy comment from original code)

	dbentry = artist_dict_to_db(artistupdatedict, dbconn=dbconn)
	dbentry = {k: v for k, v in dbentry.items() if v}

	existing_artist_id = get_artist_id(changedartist, create_new=False, dbconn=dbconn)
	if existing_artist_id not in (None, artist_id):
		raise exc.ArtistExists(changedartist)

	op = DB['artists'].update().where(
		DB['artists'].c.id == artist_id
	).values(
		**dbentry
	)
	result = dbconn.execute(op)
	return True
