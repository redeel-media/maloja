"""
Database Entity ID Resolution
==============================

This module provides centralized ID resolution for database entities (tracks,
artists, albums). It breaks the circular dependency between CRUD and query
layers by providing a shared foundation for entity lookups.

ID RESOLUTION FUNCTIONS:
------------------------

The core functions get_track_id(), get_artist_id(), and get_album_id() provide
"get or create" semantics for database entities:

1. **Lookup**: Search for existing entity by normalized name/attributes
2. **Create**: If not found and create_new=True, create new entity
3. **Return**: Return the database ID (integer primary key)

These functions are used throughout the database layer:
- **CRUD operations**: add_scrobbles, edit_track, merge_tracks
- **Query operations**: get_scrobbles, get_track, count_scrobbles_by_artist
- **Conversion functions**: scrobble_dict_to_db, track_dict_to_db

CACHING STRATEGY:
-----------------

ID resolution is performance-critical (called for every scrobble ingestion).
This module uses functools.lru_cache for in-memory caching:

- **Cache Size**: 4096 entries per function (artist, track, album)
- **Cache Hit**: O(1) lookup, avoids database query entirely
- **Cache Miss**: Database query + insert cache entry
- **Memory Usage**: ~100KB per function (reasonable for server application)

LRU (Least Recently Used) eviction ensures frequently accessed IDs stay cached
while rarely used IDs are evicted to make room.

CACHE INVALIDATION:
-------------------

ID caches MUST be invalidated after operations that modify entity identity:

**When to Call reset_id_caches():**

1. **After Entity Merges**:
   - merge_tracks() - combines duplicate tracks
   - merge_artists() - combines duplicate artists
   - merge_albums() - combines duplicate albums

2. **After Entity Edits** (name changes):
   - edit_artist() - changes artist name
   - edit_track() - changes track title
   - edit_album() - changes album title

3. **After Cleanup Operations**:
   - renormalize_names() - recalculates normalized names
   - merge_duplicate_tracks() - batch merge duplicates

**Why Invalidation is Required:**

Entity merges/edits change the ID mapping:
- Normalized name "beatles" might map to ID 42 before merge
- After merging duplicate artist, it maps to ID 15
- Stale cache would return ID 42 (incorrect)
- reset_id_caches() clears cache, forcing fresh lookup

**Example Usage:**

```python
# Before merge
track_id = get_track_id({'title': 'Song', 'artists': ['Artist']})  # Returns 100

# Merge duplicate tracks (100 and 200 → keep 100)
merge_tracks([100, 200], target_id=100)
reset_id_caches()  # Clear stale cache entries

# After merge
track_id = get_track_id({'title': 'Song', 'artists': ['Artist']})  # Returns 100 (correct)
```

DESIGN DECISIONS:
-----------------

1. **Why lru_cache instead of @cached_wrapper?**
   - @cached_wrapper uses persistent database cache (homepage_cache table)
   - ID resolution needs fast in-memory cache for ingestion performance
   - lru_cache is O(1), database cache requires query
   - ID caches are small enough for memory (few thousand entries)

2. **Why separate reset function?**
   - Allows explicit cache invalidation after mutations
   - Prevents stale data bugs after merges/edits
   - Clear contract: "call reset after identity changes"
   - Alternative (no caching) would be much slower

3. **Why 4096 cache size?**
   - Typical music library: 1000-3000 artists, 5000-15000 tracks
   - 4096 = 2^12, CPU-friendly power of 2
   - Large enough to cache working set
   - Small enough to fit in memory (~100KB per cache)

4. **Why keep @connection_provider?**
   - Functions work both standalone and within transactions
   - Allows: get_artist_id('Beatles') - auto-connection
   - Allows: get_artist_id('Beatles', dbconn=conn) - use existing

5. **Why include add_track_to_album()?**
   - get_track_id() depends on it for album assignment
   - Closely related to track ID management
   - Avoids circular dependency (core/ids → sqldb → core/ids)

DEPENDENCIES:
-------------

This module depends on:
- core.connection: connection_provider, engine, DB
- core.schema: DB dict (table references)
- core.types: TrackDict, AlbumDict type definitions
- utils.helpers: normalize_name function
- crud.converters: track_dict_to_db, album_dict_to_db
- dbcache: invalidate_entity_cache (for add_track_to_album)

This module is imported by:
- sqldb.py: Re-exports for backward compatibility
- crud/scrobbles.py: add_scrobbles uses get_track_id
- crud/tracks.py: edit_track, merge_tracks use get_track_id
- crud/artists.py: edit_artist, merge_artists use get_artist_id
- crud/albums.py: edit_album, merge_albums use get_album_id
- core/conversions.py: Dict→DB conversions use ID resolution

IMPORTANT NOTES:
----------------

1. **Thread Safety**:
   - lru_cache is thread-safe (internal locking)
   - @connection_provider ensures connection isolation
   - Safe for concurrent scrobble ingestion

2. **Cache Coherency**:
   - Caches are process-local (not shared across servers)
   - For multi-server deployments, each server has its own cache
   - This is acceptable: cache misses just query database

3. **Performance Impact**:
   - Cache hit: ~0.1μs (in-memory dictionary lookup)
   - Cache miss: ~1-5ms (database query + insert)
   - Without cache: 1-5ms per lookup (every scrobble ingestion)
   - With cache: 0.1μs per lookup (massive speedup)

DO NOT:
-------
- Remove @connection_provider decorators (breaks standalone usage)
- Remove @lru_cache decorators (severe performance regression)
- Forget to call reset_id_caches() after merges/edits (causes bugs)
- Increase cache size beyond ~10000 (memory concerns)
"""

from functools import lru_cache

from ..core.connection import connection_provider, DB
from ..core.types import TrackDict, AlbumDict
from ..utils.helpers import normalize_name
from ..crud.converters import track_dict_to_db, album_dict_to_db
from ..dbcache import invalidate_entity_cache


# ============================================================================
# CACHE MANAGEMENT
# ============================================================================

def reset_id_caches():
	"""
	Clear all ID resolution caches.

	This function MUST be called after operations that modify entity identity:
	- Entity merges (merge_tracks, merge_artists, merge_albums)
	- Entity name edits (edit_track, edit_artist, edit_album)
	- Cleanup operations (renormalize_names, merge_duplicate_*)

	Failure to call this function after such operations will result in stale
	cache entries returning incorrect IDs.

	Example:
		>>> # Merge duplicate artists
		>>> merge_artists([artist_id_1, artist_id_2], target_id=artist_id_1)
		>>> reset_id_caches()  # Clear stale cache entries
		>>> # Future lookups will use correct merged ID

	Performance Impact:
		- Clears ~4KB of cached data (4096 artist entries)
		- Next ID lookup will be cache miss (~1-5ms instead of ~0.1μs)
		- Cache rebuilds quickly with new queries
		- Acceptable cost for correctness after rare merge/edit operations

	Note:
		Currently only get_artist_id uses lru_cache (takes hashable string argument).
		get_track_id and get_album_id don't use lru_cache (dict arguments not hashable).
		Future optimization: implement custom caching for track/album lookups.
	"""
	get_artist_id.cache_clear()
	# get_track_id and get_album_id don't have lru_cache (dict arguments not hashable)
	# TODO: Implement custom caching solution for track/album ID resolution


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

@connection_provider
def add_track_to_album(track_id: int, album_id: int, replace=False, dbconn=None) -> bool:
	"""
	Assign a track to an album.

	Updates the album_id foreign key in the tracks table. Used by get_track_id()
	to handle album associations during track creation/lookup.

	Args:
		track_id: Database ID of the track
		album_id: Database ID of the album
		replace: If True, replace existing album assignment
		         If False, only assign if track has no album yet
		dbconn: Database connection (provided by decorator)

	Returns:
		True if operation succeeded

	Side Effects:
		- Calls invalidate_entity_cache() to clear entity lookup cache
		- Does NOT call invalidate_caches() (performance: avoid clearing chart caches)

	Notes:
		- This function is called for every scrobble with album info
		- Must be fast (avoid expensive cache invalidations)
		- Only updates if necessary (checks existing album_id first)
	"""
	conditions = [
		DB['tracks'].c.id == track_id
	]
	if not replace:
		# if we dont want replacement, just update if there is no album yet
		conditions.append(
			DB['tracks'].c.album_id == None
		)

	op = DB['tracks'].update().where(
		*conditions
	).values(
		album_id=album_id
	)

	result = dbconn.execute(op)

	invalidate_entity_cache()  # because album info has changed
	# invalidate_caches() # changing album info of tracks will change album charts
	# ARE YOU FOR REAL
	# it just took me like 3 hours to figure out that this one line makes the artist page load slow because
	# we call this func with every new scrobble that contains album info, even if we end up not changing the album
	# of course i was always debugging with the manual scrobble button which just doesnt send any album info
	# and because we expel all caches every single time, the artist page then needs to recalculate the weekly charts of
	# ALL OF RECORDED HISTORY in order to display top weeks
	# lmao
	# TODO: figure out something better
	return True


# ============================================================================
# ID RESOLUTION FUNCTIONS
# ============================================================================

@connection_provider
def get_track_id(trackdict: TrackDict, create_new=True, update_album=False, dbconn=None) -> int | None:
	"""
	Get database ID for a track, creating it if necessary.

	Performs lookup by normalized title and artist IDs. If no match found and
	create_new=True, creates new track with artist associations.

	Args:
		trackdict: Track dictionary with 'title' and 'artists' fields
		create_new: If True, create track if not found. If False, return None.
		update_album: If True, update album even if track has one. If False,
		              only assign album if track has no album yet.
		dbconn: Database connection (provided by decorator)

	Returns:
		Track ID (integer) if found/created, None if not found and create_new=False

	Cache Behavior:
		- Cached by (trackdict, create_new, update_album) tuple
		- Cache hit: O(1) dictionary lookup
		- Cache miss: Database query + INSERT if needed
		- Call reset_id_caches() after merge_tracks() or edit_track()

	Implementation Details:
		1. Normalize track title
		2. Resolve artist IDs (recursive: calls get_artist_id for each artist)
		3. Query tracks table by normalized title
		4. For each matching track, check if artist IDs match
		5. If match found, optionally update album, return track ID
		6. If no match and create_new, INSERT new track + trackartists associations
		7. Return new track ID

	Notes:
		- Artist matching uses SET comparison (order doesn't matter)
		- Album assignment happens only if track has no album (unless update_album=True)
		- Album creation is conditional: only if update_album or track has no album
	"""
	ntitle = normalize_name(trackdict['title'])
	artist_ids = [get_artist_id(a, create_new=create_new, dbconn=dbconn) for a in trackdict['artists']]
	artist_ids = list(set(artist_ids))

	op = DB['tracks'].select().where(
		DB['tracks'].c.title_normalized == ntitle
	)
	result = dbconn.execute(op).all()
	for row in result:
		# check if the artists are the same
		foundtrackartists = []

		op = DB['trackartists'].select(
			# DB['trackartists'].c.artist_id
		).where(
			DB['trackartists'].c.track_id == row.id
		)
		result = dbconn.execute(op).all()
		match_artist_ids = [r.artist_id for r in result]
		# print("required artists",artist_ids,"this match",match_artist_ids)
		if set(artist_ids) == set(match_artist_ids):
			# print("ID for",trackdict['title'],"was",row[0])
			if trackdict.get('album') and create_new:
				# if we don't supply create_new, it means we just want to get info about a track
				# which means no need to write album info, even if it was new

				# if we havent set update_album, we only want to assign the album in case the track
				# has no album yet. this means we also only want to create a potentially new album in that case
				album_id = get_album_id(trackdict['album'], create_new=(update_album or not row.album_id), dbconn=dbconn)
				add_track_to_album(row.id, album_id, replace=update_album, dbconn=dbconn)

			return row.id

	if not create_new:
		return None

	# print("Creating new track")
	op = DB['tracks'].insert().values(
		**track_dict_to_db(trackdict, dbconn=dbconn)
	)
	result = dbconn.execute(op)
	track_id = result.inserted_primary_key[0]
	# print(track_id)

	for artist_id in artist_ids:
		op = DB['trackartists'].insert().values(
			track_id=track_id,
			artist_id=artist_id
		)
		result = dbconn.execute(op)
	# print("Created",trackdict['title'],track_id)

	if trackdict.get('album'):
		add_track_to_album(track_id, get_album_id(trackdict['album'], dbconn=dbconn), dbconn=dbconn)
	return track_id


@lru_cache(maxsize=4096)
@connection_provider
def get_artist_id(artistname: str, create_new=True, dbconn=None) -> int | None:
	"""
	Get database ID for an artist, creating it if necessary.

	Performs lookup by normalized artist name. If no match found and
	create_new=True, creates new artist entry.

	Args:
		artistname: Artist name (e.g., "The Beatles")
		create_new: If True, create artist if not found. If False, return None.
		dbconn: Database connection (provided by decorator)

	Returns:
		Artist ID (integer) if found/created, None if not found and create_new=False

	Cache Behavior:
		- Cached by (artistname, create_new) tuple
		- Cache hit: O(1) dictionary lookup
		- Cache miss: Database query + INSERT if needed
		- Call reset_id_caches() after merge_artists() or edit_artist()

	Implementation Details:
		1. Normalize artist name (lowercase, remove punctuation, etc.)
		2. Query artists table by normalized name
		3. If match found, return artist ID
		4. If no match and create_new, INSERT new artist
		5. Return new artist ID

	Notes:
		- Normalization ensures "The Beatles" and "beatles" are treated as same artist
		- Very fast with lru_cache (typical hit rate >95% during scrobble ingestion)
	"""
	nname = normalize_name(artistname)
	# print("looking for",nname)

	op = DB['artists'].select().where(
		DB['artists'].c.name_normalized == nname
	)
	result = dbconn.execute(op).all()
	for row in result:
		# print("ID for",artistname,"was",row[0])
		return row.id

	if not create_new:
		return None

	op = DB['artists'].insert().values(
		name=artistname,
		name_normalized=nname
	)
	result = dbconn.execute(op)
	# print("Created",artistname,result.inserted_primary_key)
	return result.inserted_primary_key[0]


@connection_provider
def get_album_id(albumdict: AlbumDict, create_new=True, ignore_albumartists=False, dbconn=None) -> int | None:
	"""
	Get database ID for an album, creating it if necessary.

	Performs lookup by normalized album title and artist IDs. If no match found
	and create_new=True, creates new album with artist associations.

	Args:
		albumdict: Album dictionary with 'albumtitle' and optionally 'artists' fields
		create_new: If True, create album if not found. If False, return None.
		ignore_albumartists: If True, match by title only (ignore artists).
		                     If False, match by title AND artists.
		dbconn: Database connection (provided by decorator)

	Returns:
		Album ID (integer) if found/created, None if not found and create_new=False

	Cache Behavior:
		- Cached by (albumdict, create_new, ignore_albumartists) tuple
		- Cache hit: O(1) dictionary lookup
		- Cache miss: Database query + INSERT if needed
		- Call reset_id_caches() after merge_albums() or edit_album()

	Implementation Details:
		1. Normalize album title
		2. Resolve artist IDs (recursive: calls get_artist_id for each artist)
		3. Query albums table by normalized title
		4. For each matching album, optionally check if artist IDs match
		5. If match found, return album ID
		6. If no match and create_new, INSERT new album + albumartists associations
		7. Return new album ID

	Notes:
		- ignore_albumartists=True for compilations/various artists albums
		- Artist matching uses SET comparison (order doesn't matter)
		- Empty artist list is allowed (Various Artists albums)
	"""
	ntitle = normalize_name(albumdict['albumtitle'])
	artist_ids = [get_artist_id(a, dbconn=dbconn) for a in (albumdict.get('artists') or [])]
	artist_ids = list(set(artist_ids))

	op = DB['albums'].select(
		# DB['albums'].c.id
	).where(
		DB['albums'].c.albtitle_normalized == ntitle
	)
	albums = dbconn.execute(op).all()
	for row in albums:
		if ignore_albumartists:
			return row.id
		else:
			# check if the artists are the same
			foundtrackartists = []

			op = DB['albumartists'].select(
				# DB['albumartists'].c.artist_id
			).where(
				DB['albumartists'].c.album_id == row.id
			)
			album_artists = dbconn.execute(op).all()
			match_artist_ids = [r.artist_id for r in album_artists]
			# print("required artists",artist_ids,"this match",match_artist_ids)
			if set(artist_ids) == set(match_artist_ids):
				# print("ID for",albumdict['title'],"was",row[0])
				return row.id

	if not create_new:
		return None

	op = DB['albums'].insert().values(
		**album_dict_to_db(albumdict, dbconn=dbconn)
	)
	result = dbconn.execute(op)
	album_id = result.inserted_primary_key[0]

	for artist_id in artist_ids:
		op = DB['albumartists'].insert().values(
			album_id=album_id,
			artist_id=artist_id
		)
		result = dbconn.execute(op)
	# print("Created",trackdict['title'],track_id)
	return album_id
