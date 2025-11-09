"""
Scrobble Query Operations
==========================

This module contains query functions for retrieving scrobbles and related data.

ARCHITECTURE:
  This is part of the queries layer in the database module hierarchy:
  - core/ (schema, connection, types, IDs, cache)
  - crud/ (scrobbles, tracks, artists, albums, associations)
  - queries/ (scrobbles, entities, relationships) ← YOU ARE HERE
  - charts/ (specialized chart operations)

SCROBBLE QUERIES:
  This module provides functions to retrieve scrobbles with various filters:

  1. get_scrobbles(since, to, resolve_references, limit, reverse)
     - Get all scrobbles within a time range
     - Basic scrobble retrieval with time filtering

  2. get_scrobbles_of_artist(artist, since, to, resolve_references, limit, reverse, associated)
     - Get scrobbles for a specific artist
     - Supports "associated artists" mode (related artists)
     - Joins scrobbles with trackartists to filter by artist

  3. get_scrobbles_of_track(track, since, to, resolve_references, limit, reverse)
     - Get scrobbles for a specific track
     - Direct filtering by track_id

  4. get_scrobbles_of_album(album, since, to, resolve_references, limit, reverse)
     - Get scrobbles for all tracks in an album
     - Joins scrobbles → tracks → filter by album_id

  5. get_scrobbles_num(since, to)
     - Count scrobbles in a time range
     - Optimized count query (no resolution)

RELATIONSHIP QUERIES:
  Functions for querying artist-track relationships:

  6. get_artists_of_track(track_id, resolve_references)
     - Get all artists for a track
     - Queries trackartists junction table

  7. get_tracks_of_artist(artist)
     - Get all tracks by an artist
     - Joins tracks with trackartists, filters by artist_id

CACHING:
  All functions are decorated with @cached_wrapper for performance:
  - Results are cached based on function arguments
  - Cache is invalidated when database changes (via dbcache)
  - See database/dbcache.py for caching implementation

REFERENCE RESOLUTION:
  The resolve_references parameter controls data format:
  - True: Returns full dictionaries with resolved foreign keys
           (e.g., {"track": "Song Title", "artists": ["Artist Name"]})
  - False: Returns database rows with IDs
            (e.g., Row(track_id=123, artist_id=456))

  Resolution uses converter functions from crud.converters:
  - scrobbles_db_to_dict: Converts scrobble rows to ScrobbleDict
  - tracks_db_to_dict: Converts track rows to TrackDict
  - artists_db_to_dict: Converts artist rows to artist names

TIME FILTERING:
  Most functions accept since/to parameters (Unix timestamps):
  - since: Start of time range (default: 0 = beginning of time)
  - to: End of time range (default: now() = current time)
  - Inclusive range: since <= timestamp <= to

ORDERING:
  The reverse parameter controls result ordering:
  - False (default): Ascending by timestamp (oldest first)
  - True: Descending by timestamp (newest first)

LIMITING:
  The limit parameter controls result count:
  - None (default): Return all results
  - N: Return first N results (after ordering)
  - For associated artist queries: Deduplication happens before limiting

ASSOCIATED ARTISTS:
  get_scrobbles_of_artist has an "associated" mode:
  - When True: Includes scrobbles from associated artists (similar/related)
  - Uses get_associated_artists() to find related artists
  - Deduplicates by timestamp (multiple associated artists on same track)
  - Example: Red Velvet scrobbles include Irene & Seulgi sub-unit

  Implementation:
  1. Get main artist ID + associated artist IDs
  2. Query trackartists for any of these artist IDs
  3. Join with scrobbles table
  4. Remove duplicate timestamps (same scrobble, multiple matching artists)
  5. Apply limit after deduplication

CIRCULAR IMPORT HANDLING:
  This module imports from sqldb to access functions not yet extracted:
  - get_associated_artists (will move to queries/relationships.py in Phase 5)
  - get_artist (will move to queries/relationships.py in Phase 5)

  These are imported locally within functions to avoid circular dependencies:
  - sqldb imports queries.scrobbles for re-export
  - queries.scrobbles needs these functions temporarily
  - Local imports break the circular dependency

DEPENDENCIES:
  - core.schema: DB (table definitions)
  - core.connection: connection_provider decorator
  - core.ids: get_artist_id, get_track_id, get_album_id (ID resolution)
  - crud.converters: scrobbles_db_to_dict, tracks_db_to_dict (data conversion)
  - utils.helpers: now() (current timestamp)
  - dbcache: cached_wrapper (result caching)
  - sqlalchemy: SQL operations
  - sqldb: get_associated_artists, get_artist (temporary, will be refactored)

EXPORTED FUNCTIONS:
  - get_scrobbles(since=None, to=None, resolve_references=True, limit=None, reverse=False, dbconn=None)
      Get all scrobbles in time range
  - get_scrobbles_of_artist(artist, since=None, to=None, resolve_references=True, limit=None, reverse=False, associated=False, dbconn=None)
      Get scrobbles for an artist (with optional associated artists)
  - get_scrobbles_of_track(track, since=None, to=None, resolve_references=True, limit=None, reverse=False, dbconn=None)
      Get scrobbles for a track
  - get_scrobbles_of_album(album, since=None, to=None, resolve_references=True, limit=None, reverse=False, dbconn=None)
      Get scrobbles for an album
  - get_scrobbles_num(since=None, to=None, dbconn=None)
      Count scrobbles in time range
  - get_artists_of_track(track_id, resolve_references=True, dbconn=None)
      Get artists for a track
  - get_tracks_of_artist(artist, dbconn=None)
      Get tracks for an artist

USAGE EXAMPLE:
  from maloja.database.queries import scrobbles

  # Get all scrobbles in 2024
  from maloja.utils.helpers import parse_timestamp
  scrobbles_2024 = scrobbles.get_scrobbles(
      since=parse_timestamp("2024-01-01"),
      to=parse_timestamp("2024-12-31")
  )

  # Get recent scrobbles for an artist (newest first)
  artist_scrobbles = scrobbles.get_scrobbles_of_artist(
      artist="Taylor Swift",
      limit=100,
      reverse=True
  )

  # Get scrobbles including associated artists
  associated_scrobbles = scrobbles.get_scrobbles_of_artist(
      artist="Red Velvet",
      associated=True,  # Includes Irene & Seulgi, etc.
      limit=50
  )

  # Get all artists for a track
  track_artists = scrobbles.get_artists_of_track(
      track_id=123,
      resolve_references=True  # Returns artist names
  )

  # Count scrobbles in a time range
  scrobble_count = scrobbles.get_scrobbles_num(
      since=start_timestamp,
      to=end_timestamp
  )

MIGRATION NOTES:
  This module was extracted from database/sqldb.py as part of Phase 5
  refactoring. Original location: sqldb.py lines 226-400 (scrobble query
  functions).

  Temporary circular imports:
  - get_associated_artists: Will move to queries/relationships.py (Phase 5)
  - get_artist: Will move to queries/relationships.py (Phase 5)

  After Phase 5 completion, update these imports to:
  - from .relationships import get_associated_artists, get_artist

SEE ALSO:
  - database/__init__.py: Wrapper functions that handle cache invalidation
  - database/sqldb.py: Re-exports for backward compatibility
  - core/ids.py: ID resolution functions
  - crud/converters.py: Database row converters
  - queries/entities.py: Entity listing queries (Phase 5)
  - queries/relationships.py: Relationship queries (Phase 5)
  - docs/caching_strategy.md: Caching architecture
"""

import sqlalchemy as sql

from ..dbcache import cached_wrapper
from ..core.schema import DB
from ..core.connection import connection_provider
from ..core.ids import get_artist_id, get_track_id, get_album_id
from ..utils.helpers import now
from ..crud.converters import scrobbles_db_to_dict, tracks_db_to_dict


# ============================================================================
# SCROBBLE RETRIEVAL QUERIES
# ============================================================================

@cached_wrapper
@connection_provider
def get_scrobbles_of_artist(artist,since=None,to=None,resolve_references=True,limit=None,reverse=False,associated=False,dbconn=None):
	"""
	Get scrobbles for a specific artist.

	This queries the scrobbles table joined with trackartists to find all
	scrobbles of tracks by the given artist. Supports "associated artists"
	mode to include scrobbles from similar/related artists.

	Args:
		artist: Artist name (string) or artist dict
		since: Start timestamp (Unix time, default: 0)
		to: End timestamp (Unix time, default: now())
		resolve_references: If True, return full dicts; if False, return DB rows
		limit: Maximum number of results (default: None = all)
		reverse: If True, order newest first; if False, oldest first
		associated: If True, include associated artists' scrobbles
		dbconn: Database connection (provided by decorator)

	Returns:
		List of ScrobbleDict (if resolve_references=True) or DB rows

	Associated Artists Mode:
		When associated=True, includes scrobbles from related artists:
		1. Get main artist ID + associated artist IDs
		2. Query for any of these artists
		3. Deduplicate by timestamp (same scrobble, multiple matching artists)
		4. Apply limit after deduplication

		Example: "Red Velvet" with associated=True includes scrobbles from
		         "Irene & Seulgi" (sub-unit)

	Deduplication:
		When associated=True, the same scrobble may match multiple artists
		(e.g., a song by Irene & Seulgi matches both "Red Velvet" and the
		individual members). We deduplicate by timestamp to count each
		scrobble only once.

	Example:
		# Get recent scrobbles for Taylor Swift
		scrobbles = get_scrobbles_of_artist(
			artist="Taylor Swift",
			limit=100,
			reverse=True
		)

		# Get all scrobbles including associated artists
		scrobbles = get_scrobbles_of_artist(
			artist="Red Velvet",
			associated=True
		)
	"""

	# Import get_associated_artists from relationships module
	# Phase 5: Moved to queries/relationships.py, no longer in sqldb
	from .relationships import get_associated_artists

	if since is None: since=0
	if to is None: to=now()

	if associated:
		artist_ids = get_associated_artists(artist,resolve_ids=False,dbconn=dbconn) + [get_artist_id(artist,create_new=False,dbconn=dbconn)]
	else:
		artist_ids = [get_artist_id(artist,create_new=False,dbconn=dbconn)]


	jointable = sql.join(DB['scrobbles'],DB['trackartists'],DB['scrobbles'].c.track_id == DB['trackartists'].c.track_id)

	op = jointable.select().where(
		DB['scrobbles'].c.timestamp.between(since,to),
		DB['trackartists'].c.artist_id.in_(artist_ids)
	)
	if reverse:
		op = op.order_by(sql.desc('timestamp'))
	else:
		op = op.order_by(sql.asc('timestamp'))
	if limit and not associated:
		# if we count associated we cant limit here because we remove stuff later!
		op = op.limit(limit)
	result = dbconn.execute(op).all()

	# remove duplicates (multiple associated artists in the song, e.g. Irene & Seulgi being both counted as Red Velvet)
	# distinct on doesn't seem to exist in sqlite
	if associated:
		seen = set()
		filtered_result = []
		for row in result:
			if row.timestamp not in seen:
				filtered_result.append(row)
				seen.add(row.timestamp)
		result = filtered_result
		if limit:
			result = result[:limit]



	if resolve_references:
		result = scrobbles_db_to_dict(result,dbconn=dbconn)
	#result = [scrobble_db_to_dict(row,resolve_references=resolve_references) for row in result]
	return result

@cached_wrapper
@connection_provider
def get_scrobbles_of_track(track,since=None,to=None,resolve_references=True,limit=None,reverse=False,dbconn=None):
	"""
	Get scrobbles for a specific track.

	This queries the scrobbles table filtered by track_id. All scrobbles
	of the given track within the time range are returned.

	Args:
		track: Track dict (with 'title' and 'artists' keys)
		since: Start timestamp (Unix time, default: 0)
		to: End timestamp (Unix time, default: now())
		resolve_references: If True, return full dicts; if False, return DB rows
		limit: Maximum number of results (default: None = all)
		reverse: If True, order newest first; if False, oldest first
		dbconn: Database connection (provided by decorator)

	Returns:
		List of ScrobbleDict (if resolve_references=True) or DB rows

	Example:
		# Get all scrobbles for a track
		scrobbles = get_scrobbles_of_track(
			track={
				'title': 'Bohemian Rhapsody',
				'artists': ['Queen']
			}
		)

		# Get recent 50 scrobbles
		recent = get_scrobbles_of_track(
			track=track_dict,
			limit=50,
			reverse=True
		)
	"""

	if since is None: since=0
	if to is None: to=now()

	track_id = get_track_id(track,create_new=False,dbconn=dbconn)


	op = DB['scrobbles'].select().where(
		DB['scrobbles'].c.timestamp<=to,
		DB['scrobbles'].c.timestamp>=since,
		DB['scrobbles'].c.track_id==track_id
	)
	if reverse:
		op = op.order_by(sql.desc('timestamp'))
	else:
		op = op.order_by(sql.asc('timestamp'))
	if limit:
		op = op.limit(limit)
	result = dbconn.execute(op).all()

	if resolve_references:
		result = scrobbles_db_to_dict(result)
	#result = [scrobble_db_to_dict(row) for row in result]
	return result

@cached_wrapper
@connection_provider
def get_scrobbles_of_album(album,since=None,to=None,resolve_references=True,limit=None,reverse=False,dbconn=None):
	"""
	Get scrobbles for all tracks in an album.

	This queries scrobbles joined with tracks, filtered by album_id.
	All scrobbles of tracks in the given album are returned.

	Args:
		album: Album dict (with 'albumtitle' and 'artists' keys)
		since: Start timestamp (Unix time, default: 0)
		to: End timestamp (Unix time, default: now())
		resolve_references: If True, return full dicts; if False, return DB rows
		limit: Maximum number of results (default: None = all)
		reverse: If True, order newest first; if False, oldest first
		dbconn: Database connection (provided by decorator)

	Returns:
		List of ScrobbleDict (if resolve_references=True) or DB rows

	Example:
		# Get all scrobbles for an album
		scrobbles = get_scrobbles_of_album(
			album={
				'albumtitle': 'Abbey Road',
				'artists': ['The Beatles']
			}
		)
	"""

	if since is None: since=0
	if to is None: to=now()

	album_id = get_album_id(album,create_new=False,dbconn=dbconn)

	jointable = sql.join(DB['scrobbles'],DB['tracks'],DB['scrobbles'].c.track_id == DB['tracks'].c.id)

	op = jointable.select().where(
		DB['scrobbles'].c.timestamp<=to,
		DB['scrobbles'].c.timestamp>=since,
		DB['tracks'].c.album_id==album_id
	)
	if reverse:
		op = op.order_by(sql.desc('timestamp'))
	else:
		op = op.order_by(sql.asc('timestamp'))
	if limit:
		op = op.limit(limit)
	result = dbconn.execute(op).all()

	if resolve_references:
		result = scrobbles_db_to_dict(result)
	#result = [scrobble_db_to_dict(row) for row in result]
	return result

@cached_wrapper
@connection_provider
def get_scrobbles(since=None,to=None,resolve_references=True,limit=None,reverse=False,dbconn=None):
	"""
	Get all scrobbles within a time range.

	This is the most basic scrobble query - simply returns all scrobbles
	in the database filtered by timestamp.

	Args:
		since: Start timestamp (Unix time, default: 0)
		to: End timestamp (Unix time, default: now())
		resolve_references: If True, return full dicts; if False, return DB rows
		limit: Maximum number of results (default: None = all)
		reverse: If True, order newest first; if False, oldest first
		dbconn: Database connection (provided by decorator)

	Returns:
		List of ScrobbleDict (if resolve_references=True) or DB rows

	Example:
		# Get all scrobbles in 2024
		scrobbles = get_scrobbles(
			since=1704067200,  # 2024-01-01
			to=1735689599      # 2024-12-31
		)

		# Get latest 100 scrobbles
		recent = get_scrobbles(
			limit=100,
			reverse=True
		)
	"""


	if since is None: since=0
	if to is None: to=now()

	op = DB['scrobbles'].select().where(
		DB['scrobbles'].c.timestamp.between(since,to)
	)
	if reverse:
		op = op.order_by(sql.desc('timestamp'))
	else:
		op = op.order_by(sql.asc('timestamp'))
	if limit:
		op = op.limit(limit)


	result = dbconn.execute(op).all()

	if resolve_references:
		result = scrobbles_db_to_dict(result,dbconn=dbconn)
	#result = [scrobble_db_to_dict(row,resolve_references=resolve_references) for i,row in enumerate(result) if i<max]

	return result


# we can do that with above and resolve_references=False, but just testing speed
@cached_wrapper
@connection_provider
def get_scrobbles_num(since=None,to=None,dbconn=None):
	"""
	Count scrobbles in a time range.

	This is an optimized count query that doesn't resolve references or
	return actual scrobble data - just the count.

	Args:
		since: Start timestamp (Unix time, default: 0)
		to: End timestamp (Unix time, default: now())
		dbconn: Database connection (provided by decorator)

	Returns:
		Integer count of scrobbles

	Performance:
		This is faster than len(get_scrobbles(..., resolve_references=False))
		because it uses SQL COUNT() and doesn't transfer row data.

	Example:
		# Count scrobbles in 2024
		count = get_scrobbles_num(
			since=1704067200,  # 2024-01-01
			to=1735689599      # 2024-12-31
		)
		print(f"Total scrobbles: {count}")
	"""

	if since is None: since=0
	if to is None: to=now()

	op = sql.select(sql.func.count()).select_from(DB['scrobbles']).where(
		DB['scrobbles'].c.timestamp.between(since,to)
	)
	result = dbconn.execute(op).all()

	return result[0][0]


# ============================================================================
# ARTIST-TRACK RELATIONSHIP QUERIES
# ============================================================================

@cached_wrapper
@connection_provider
def get_artists_of_track(track_id,resolve_references=True,dbconn=None):
	"""
	Get all artists for a track.

	This queries the trackartists junction table to find all artists
	associated with the given track_id.

	Args:
		track_id: Track ID (integer)
		resolve_references: If True, return artist names; if False, return artist IDs
		dbconn: Database connection (provided by decorator)

	Returns:
		List of artist names (strings) if resolve_references=True
		List of artist IDs (integers) if resolve_references=False

	Example:
		# Get artist names for a track
		artists = get_artists_of_track(track_id=123)
		# Returns: ['Artist A', 'Artist B']

		# Get artist IDs only
		artist_ids = get_artists_of_track(
			track_id=123,
			resolve_references=False
		)
		# Returns: [456, 789]
	"""

	# Import get_artist from relationships module
	# Phase 5: Moved to queries/relationships.py, no longer in sqldb
	from .relationships import get_artist

	op = DB['trackartists'].select().where(
		DB['trackartists'].c.track_id==track_id
	)
	result = dbconn.execute(op).all()

	artists = [get_artist(row.artist_id,dbconn=dbconn) if resolve_references else row.artist_id for row in result]
	return artists


@cached_wrapper
@connection_provider
def get_tracks_of_artist(artist,dbconn=None):
	"""
	Get all tracks by an artist.

	This queries the tracks table joined with trackartists to find all
	tracks by the given artist.

	Args:
		artist: Artist name (string) or artist dict
		dbconn: Database connection (provided by decorator)

	Returns:
		List of TrackDict with full track information

	Example:
		# Get all tracks by an artist
		tracks = get_tracks_of_artist(artist="Taylor Swift")
		# Returns: [
		#   {'title': 'Song 1', 'artists': ['Taylor Swift'], ...},
		#   {'title': 'Song 2', 'artists': ['Taylor Swift'], ...}
		# ]
	"""

	artist_id = get_artist_id(artist,dbconn=dbconn)

	op = sql.join(DB['tracks'],DB['trackartists']).select().where(
		DB['trackartists'].c.artist_id==artist_id
	)
	result = dbconn.execute(op).all()

	return tracks_db_to_dict(result,dbconn=dbconn)
