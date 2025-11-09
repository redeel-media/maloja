"""
Relationship Query Operations
==============================

This module contains query functions for entity relationships and single-entity retrieval.

ARCHITECTURE:
  This is part of the queries layer in the database module hierarchy:
  - core/ (schema, connection, types, IDs, cache)
  - crud/ (scrobbles, tracks, artists, albums, associations)
  - queries/ (scrobbles, entities, relationships) ← YOU ARE HERE
  - charts/ (specialized chart operations)

FUNCTION GROUPS:
  This module contains 4 distinct groups of functions:

  1. BATCHED RELATIONSHIP QUERIES (Many-to-many resolution):
     - get_artists_of_tracks(): Get artists for multiple tracks (track_id → [artists])
     - get_artists_of_albums(): Get artists for multiple albums (album_id → [artists])
     - get_albums_of_artists(): Get albums for multiple artists (artist_id → [albums])
     - get_albums_artists_appear_on(): Get albums artists appear on (artist_id → [albums])

  2. ID MAPPING QUERIES (Bulk entity retrieval):
     - get_tracks_map(): Get tracks by IDs (track_id → TrackDict)
     - get_artists_map(): Get artists by IDs (artist_id → artist name)
     - get_albums_map(): Get albums by IDs (album_id → AlbumDict)

  3. ASSOCIATION QUERIES (Artist associations):
     - get_associated_artists(): Get associated artists (related/similar artists)
     - get_associated_artist_map(): Get associated artists for multiple artists
     - get_credited_artists(): Get artists credited on another artist's work

  4. SINGLE ENTITY GETTERS (By ID):
     - get_track(): Get a single track by ID
     - get_artist(): Get a single artist by ID
     - get_album(): Get a single album by ID
     - get_scrobble(): Get a single scrobble by timestamp

BATCHED QUERIES AND SQLITE LIMITS:
  The batched relationship queries implement critical performance optimizations
  for SQLite's parameter limits.

  SQLite has SQLITE_MAX_VARIABLE_NUMBER limit (default 32,766). When IN clauses
  exceed this limit, performance degrades catastrophically:
  - Normal: 50,000 tracks/second
  - Over limit: 400 tracks/second (125x slower!)

  Solution: Batch queries in chunks of 999 parameters (well under the limit).

  Example (get_artists_of_tracks with 10,000 track IDs):
  - Without batching: Single query with 10,000 parameters → Very slow
  - With batching: 11 queries with 999 parameters each → Very fast

  Functions using batching:
  - get_artists_of_tracks: Batches track IDs
  - get_artists_of_albums: Batches album IDs
  - get_albums_of_artists: Batches artist IDs

CACHING STRATEGIES:
  Two caching decorators are used:

  1. @cached_wrapper_individual:
     - For batched queries (get_artists_of_tracks, etc.)
     - For ID mapping queries (get_tracks_map, etc.)
     - Caches INDIVIDUAL results, not batch results
     - Example: get_tracks_map([1,2,3]) caches track 1, 2, 3 separately
     - Next call to get_tracks_map([2,3,4]) reuses cached 2 and 3

  2. @cached_wrapper:
     - For single entity getters (get_track, get_artist, etc.)
     - For association queries (get_associated_artists, etc.)
     - Caches entire result based on parameters
     - Example: get_track(123) caches the entire track

DEDUPLICATION:
  Some queries include deduplication logic:

  get_albums_artists_appear_on:
  - An artist may appear on multiple tracks in the same album
  - Without deduplication: Album appears multiple times in result
  - With deduplication: Album appears once per artist
  - Implementation: Track (artist_id, album_id) pairs in already_done dict

ID RESOLUTION VS RAW IDS:
  Many functions support resolve_ids parameter:
  - resolve_ids=True: Return full entities (names, dicts)
  - resolve_ids=False: Return just IDs (integers)

  Example (get_associated_artists):
  - resolve_ids=True: Returns ['Artist A', 'Artist B']
  - resolve_ids=False: Returns [123, 456]

ARTIST ASSOCIATIONS:
  The database tracks artist associations (similar/related artists):

  associated_artists table:
  - source_artist: The associated/similar artist ID
  - target_artist: The main artist ID
  - Example: Irene & Seulgi (source) associated with Red Velvet (target)

  Three query types:
  1. get_associated_artists: Given main artists, get their associated artists
     - Input: ['Red Velvet']
     - Output: ['Irene & Seulgi', 'Joy', 'Wendy & Seulgi']

  2. get_associated_artist_map: Same but returns dict for multiple artists
     - Input: ['Red Velvet', 'BTS']
     - Output: {
         'Red Velvet': ['Irene & Seulgi', ...],
         'BTS': ['J-Hope', 'RM', ...]
       }

  3. get_credited_artists: Reverse - given associated artist, get main artists
     - Input: ['Irene & Seulgi']
     - Output: ['Red Velvet']

DEPENDENCIES:
  - core.schema: DB (table definitions)
  - core.connection: connection_provider decorator
  - core.types: TrackDict, AlbumDict, ScrobbleDict type definitions
  - core.ids: get_artist_id (for ID resolution)
  - crud.converters: Entity converters (track_db_to_dict, etc.)
  - dbcache: cached_wrapper, cached_wrapper_individual (result caching)
  - sqlalchemy: SQL operations

EXPORTED FUNCTIONS (14 total):

  Batched Relationship Queries:
  - get_artists_of_tracks(track_ids, dbconn=None) -> dict[int, list[str]]
  - get_artists_of_albums(album_ids, dbconn=None) -> dict[int, list[str]]
  - get_albums_of_artists(artist_ids, dbconn=None) -> dict[int, list[AlbumDict]]
  - get_albums_artists_appear_on(artist_ids, dbconn=None) -> dict[int, list[AlbumDict]]

  ID Mapping Queries:
  - get_tracks_map(track_ids, dbconn=None) -> dict[int, TrackDict]
  - get_artists_map(artist_ids, dbconn=None) -> dict[int, str]
  - get_albums_map(album_ids, dbconn=None) -> dict[int, AlbumDict]

  Association Queries:
  - get_associated_artists(*artists, resolve_ids=True, dbconn=None) -> list[str] | list[int]
  - get_associated_artist_map(artists=[], artist_ids=None, resolve_ids=True, dbconn=None) -> dict
  - get_credited_artists(*artists, dbconn=None) -> list[str]

  Single Entity Getters:
  - get_track(track_id: int, dbconn=None) -> TrackDict
  - get_artist(artist_id: int, dbconn=None) -> str
  - get_album(album_id: int, dbconn=None) -> AlbumDict
  - get_scrobble(timestamp: int, include_internal=False, dbconn=None) -> ScrobbleDict

USAGE EXAMPLES:

  # Batched relationship query
  from maloja.database.queries import relationships

  track_ids = [1, 2, 3, 4, 5]
  artists_map = relationships.get_artists_of_tracks(track_ids)
  # Returns: {
  #   1: ['Artist A'],
  #   2: ['Artist A', 'Artist B'],
  #   3: ['Artist C'],
  #   4: ['Artist D'],
  #   5: ['Artist E', 'Artist F']
  # }

  # ID mapping query
  tracks = relationships.get_tracks_map([1, 2, 3])
  # Returns: {
  #   1: {'title': 'Song 1', 'artists': ['Artist A'], ...},
  #   2: {'title': 'Song 2', 'artists': ['Artist B'], ...},
  #   3: {'title': 'Song 3', 'artists': ['Artist C'], ...}
  # }

  # Association query
  associated = relationships.get_associated_artists('Red Velvet')
  # Returns: ['Irene & Seulgi', 'Joy', 'Wendy & Seulgi']

  # Single entity getter
  track = relationships.get_track(track_id=123)
  # Returns: {'title': 'Song Title', 'artists': ['Artist'], ...}

MIGRATION NOTES:
  This module was extracted from database/sqldb.py as part of Phase 5
  refactoring. Original location: sqldb.py lines 1799-2131 (relationship
  and single-entity query functions).

  After Phase 5 completion, update circular imports in queries/scrobbles.py:
  - Change local imports of get_artist, get_associated_artists
  - To: from .relationships import get_artist, get_associated_artists

SEE ALSO:
  - database/__init__.py: Wrapper functions that handle cache invalidation
  - database/sqldb.py: Re-exports for backward compatibility
  - queries/scrobbles.py: Scrobble retrieval queries (imports from here)
  - queries/entities.py: Entity listing queries
  - crud/converters.py: Database row converters
  - docs/batched_queries.md: Batching strategy for performance
  - docs/caching_strategy.md: Caching architecture
"""

import sqlalchemy as sql

from ..dbcache import cached_wrapper, cached_wrapper_individual
from ..core.schema import DB
from ..core.connection import connection_provider
from ..core.types import TrackDict, AlbumDict, ScrobbleDict
from ..core.ids import get_artist_id
from ..crud.converters import (
	artist_db_to_dict, track_db_to_dict, album_db_to_dict,
	artists_db_to_dict, tracks_db_to_dict, albums_db_to_dict,
	scrobbles_db_to_dict
)


# ============================================================================
# BATCHED RELATIONSHIP QUERIES
# ============================================================================

@cached_wrapper_individual
@connection_provider
def get_artists_of_tracks(track_ids,dbconn=None):
	"""
	Get artists for tracks, batching queries to avoid SQLite parameter limits.

	SQLite has SQLITE_MAX_VARIABLE_NUMBER limit (default 32766). When IN clauses
	exceed this, performance degrades catastrophically (50k tracks/sec → 400 tracks/sec).
	We batch in chunks of 999 to stay well under the limit.
	"""
	jointable = sql.join(
		DB['trackartists'],
		DB['artists']
	)

	track_ids_list = list(track_ids)
	artists = {}

	# Batch size: 999 is safe (well under 32766 limit)
	# This prevents catastrophic slowdown when track_ids > 32k
	BATCH_SIZE = 999

	for i in range(0, len(track_ids_list), BATCH_SIZE):
		batch = track_ids_list[i:i + BATCH_SIZE]

		# we need to select to avoid multiple 'id' columns that will then
		# be misinterpreted by the row-dict converter
		op = sql.select(
			DB['artists'],
			DB['trackartists'].c.track_id
		).select_from(jointable).where(
			DB['trackartists'].c.track_id.in_(batch)
		)

		result = dbconn.execute(op).all()

		for row in result:
			artists.setdefault(row.track_id,[]).append(artist_db_to_dict(row,dbconn=dbconn))

	return artists

@cached_wrapper_individual
@connection_provider
def get_artists_of_albums(album_ids,dbconn=None):
	"""Batch queries to avoid SQLite parameter limits"""
	jointable = sql.join(
		DB['albumartists'],
		DB['artists']
	)

	album_ids_list = list(album_ids)
	artists = {}
	BATCH_SIZE = 999

	for i in range(0, len(album_ids_list), BATCH_SIZE):
		batch = album_ids_list[i:i + BATCH_SIZE]

		# we need to select to avoid multiple 'id' columns that will then
		# be misinterpreted by the row-dict converter
		op = sql.select(
			DB['artists'],
			DB['albumartists'].c.album_id
		).select_from(jointable).where(
			DB['albumartists'].c.album_id.in_(batch)
		)
		result = dbconn.execute(op).all()

		for row in result:
			artists.setdefault(row.album_id,[]).append(artist_db_to_dict(row,dbconn=dbconn))

	return artists

@cached_wrapper_individual
@connection_provider
def get_albums_of_artists(artist_ids,dbconn=None):
	"""Batch queries to avoid SQLite parameter limits"""
	jointable = sql.join(
		DB['albumartists'],
		DB['albums']
	)

	artist_ids_list = list(artist_ids)
	albums = {}
	BATCH_SIZE = 999

	for i in range(0, len(artist_ids_list), BATCH_SIZE):
		batch = artist_ids_list[i:i + BATCH_SIZE]

		# we need to select to avoid multiple 'id' columns that will then
		# be misinterpreted by the row-dict converter
		op = sql.select(
			DB["albums"],
			DB['albumartists'].c.artist_id
		).select_from(jointable).where(
			DB['albumartists'].c.artist_id.in_(batch)
		)
		result = dbconn.execute(op).all()

		for row in result:
			albums.setdefault(row.artist_id,[]).append(album_db_to_dict(row,dbconn=dbconn))

	return albums

@cached_wrapper_individual
@connection_provider
# this includes the artists' own albums!
def get_albums_artists_appear_on(artist_ids,dbconn=None):

	jointable1 = sql.join(
		DB["trackartists"],
		DB["tracks"]
	)
	jointable2 = sql.join(
		jointable1,
		DB["albums"]
	)

	# we need to select to avoid multiple 'id' columns that will then
	# be misinterpreted by the row-dict converter
	op = sql.select(
		DB["albums"],
		DB["trackartists"].c.artist_id
	).select_from(jointable2).where(
		DB['trackartists'].c.artist_id.in_(artist_ids)
	)
	result = dbconn.execute(op).all()

	albums = {}
	# avoid duplicates from multiple tracks in album by same artist
	already_done = {}
	for row in result:
		if row.id in already_done.setdefault(row.artist_id,[]):
			pass
		else:
			albums.setdefault(row.artist_id,[]).append(album_db_to_dict(row,dbconn=dbconn))
			already_done[row.artist_id].append(row.id)
	return albums


# ============================================================================
# ID MAPPING QUERIES
# ============================================================================

@cached_wrapper_individual
@connection_provider
def get_tracks_map(track_ids,dbconn=None):
	op = DB['tracks'].select().where(
		DB['tracks'].c.id.in_(track_ids)
	)
	result = dbconn.execute(op).all()

	tracks = {}
	result = list(result)
	# this will get a list of artistdicts in the correct order of our rows
	trackdicts = tracks_db_to_dict(result,dbconn=dbconn)

	for row,trackdict in zip(result,trackdicts):
		tracks[row.id] = trackdict

	return tracks

@cached_wrapper_individual
@connection_provider
def get_artists_map(artist_ids,dbconn=None):

	op = DB['artists'].select().where(
		DB['artists'].c.id.in_(artist_ids)
	)
	result = dbconn.execute(op).all()

	artists = {}
	result = list(result)
	# this will get a list of artistdicts in the correct order of our rows
	artistdicts = artists_db_to_dict(result,dbconn=dbconn)
	for row,artistdict in zip(result,artistdicts):
		artists[row.id] = artistdict
	return artists


@cached_wrapper_individual
@connection_provider
def get_albums_map(album_ids,dbconn=None):
	op = DB['albums'].select().where(
		DB['albums'].c.id.in_(album_ids)
	)
	result = dbconn.execute(op).all()

	albums = {}
	result = list(result)
	# this will get a list of albumdicts in the correct order of our rows
	albumdicts = albums_db_to_dict(result,dbconn=dbconn)

	for row,albumdict in zip(result,albumdicts):
		albums[row.id] = albumdict

	return albums


# ============================================================================
# ASSOCIATION QUERIES
# ============================================================================

@cached_wrapper
@connection_provider
def get_associated_artists(*artists,resolve_ids=True,dbconn=None):
	artist_ids = [get_artist_id(a,dbconn=dbconn) for a in artists]

	jointable = sql.join(
		DB['associated_artists'],
		DB['artists'],
		DB['associated_artists'].c.source_artist == DB['artists'].c.id
	)

	# we need to select to avoid multiple 'id' columns that will then
	# be misinterpreted by the row-dict converter
	op = sql.select(
		DB['artists']
	).select_from(jointable).where(
		DB['associated_artists'].c.target_artist.in_(artist_ids)
	)
	result = dbconn.execute(op).all()

	if resolve_ids:
		artists = artists_db_to_dict(result,dbconn=dbconn)
		return artists
	else:
		return [a.id for a in result]

@cached_wrapper
@connection_provider
def get_associated_artist_map(artists=[],artist_ids=None,resolve_ids=True,dbconn=None):

	ids_supplied = (artist_ids is not None)

	if not ids_supplied:
		artist_ids = [get_artist_id(a,dbconn=dbconn) for a in artists]


	jointable = sql.join(
		DB['associated_artists'],
		DB['artists'],
		DB['associated_artists'].c.source_artist == DB['artists'].c.id
	)

	# we need to select to avoid multiple 'id' columns that will then
	# be misinterpreted by the row-dict converter
	op = sql.select(
		DB['artists'],
		DB['associated_artists'].c.target_artist
	).select_from(jointable).where(
		DB['associated_artists'].c.target_artist.in_(artist_ids)
	)
	result = dbconn.execute(op).all()

	artists_to_associated = {a_id:[] for a_id in artist_ids}
	for row in result:
		if resolve_ids:
			artists_to_associated[row.target_artist].append(artists_db_to_dict([row],dbconn=dbconn)[0])
		else:
			artists_to_associated[row.target_artist].append(row.id)

	if not ids_supplied:
		# if we supplied the artists, we want to convert back for the result
		artists_to_associated = {artists[artist_ids.index(k)]:v for k,v in artists_to_associated.items()}

	return artists_to_associated


@cached_wrapper
@connection_provider
def get_credited_artists(*artists,dbconn=None):
	artist_ids = [get_artist_id(a,dbconn=dbconn) for a in artists]

	jointable = sql.join(
		DB['associated_artists'],
		DB['artists'],
		DB['associated_artists'].c.target_artist == DB['artists'].c.id
	)

	# we need to select to avoid multiple 'id' columns that will then
	# be misinterpreted by the row-dict converter
	op = sql.select(
		DB['artists']
	).select_from(jointable).where(
		DB['associated_artists'].c.source_artist.in_(artist_ids)
	)
	result = dbconn.execute(op).all()

	artists = artists_db_to_dict(result,dbconn=dbconn)
	return artists


# ============================================================================
# SINGLE ENTITY GETTERS
# ============================================================================

@cached_wrapper
@connection_provider
def get_track(track_id: int, dbconn=None) -> TrackDict:
	op = DB['tracks'].select().where(
		DB['tracks'].c.id == track_id
	)
	result = dbconn.execute(op).all()

	trackinfo = result[0]
	return track_db_to_dict(trackinfo, dbconn=dbconn)


@cached_wrapper
@connection_provider
def get_artist(artist_id: int, dbconn=None) -> str:
	op = DB['artists'].select().where(
		DB['artists'].c.id == artist_id
	)
	result = dbconn.execute(op).all()

	artistinfo = result[0]
	return artist_db_to_dict(artistinfo, dbconn=dbconn)


@cached_wrapper
@connection_provider
def get_album(album_id: int, dbconn=None) -> AlbumDict:
	op = DB['albums'].select().where(
		DB['albums'].c.id == album_id
	)
	result = dbconn.execute(op).all()

	albuminfo = result[0]
	return album_db_to_dict(albuminfo, dbconn=dbconn)


@cached_wrapper
@connection_provider
def get_scrobble(timestamp: int, include_internal=False, dbconn=None) -> ScrobbleDict:
	op = DB['scrobbles'].select().where(
		DB['scrobbles'].c.timestamp == timestamp
	)
	result = dbconn.execute(op).all()

	scrobble = result[0]
	return scrobbles_db_to_dict(rows=[scrobble], include_internal=include_internal)[0]
