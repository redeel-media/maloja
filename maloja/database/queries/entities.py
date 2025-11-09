"""
Entity Listing Query Operations
=================================

This module contains query functions for listing all entities (artists, tracks, albums).

ARCHITECTURE:
  This is part of the queries layer in the database module hierarchy:
  - core/ (schema, connection, types, IDs, cache)
  - crud/ (scrobbles, tracks, artists, albums, associations)
  - queries/ (scrobbles, entities, relationships) ← YOU ARE HERE
  - charts/ (specialized chart operations)

ENTITY LISTING QUERIES:
  This module provides functions to retrieve all entities of each type:

  1. get_artists(dbconn=None)
     - Get all artists in the database
     - Returns list of artist name strings

  2. get_tracks(dbconn=None)
     - Get all tracks in the database
     - Returns list of TrackDict with full track information

  3. get_albums(dbconn=None)
     - Get all albums in the database
     - Returns list of AlbumDict with full album information

CACHING:
  All functions are decorated with @cached_wrapper for performance:
  - Results are cached based on function arguments
  - Cache is invalidated when database changes (via dbcache)
  - Since these functions have no parameters, they cache the entire list
  - Cache is cleared when entities are added/modified/deleted
  - See database/dbcache.py for caching implementation

REFERENCE RESOLUTION:
  All functions return fully resolved entity data:
  - get_artists(): Returns artist names (strings)
  - get_tracks(): Returns TrackDict with resolved artists, album info
  - get_albums(): Returns AlbumDict with resolved artists info

  Resolution uses converter functions from crud.converters:
  - artists_db_to_dict: Converts artist rows to list of names
  - tracks_db_to_dict: Converts track rows to TrackDict list
  - albums_db_to_dict: Converts album rows to AlbumDict list

USE CASES:
  These functions are used for:
  - Populating dropdown menus in the UI
  - Generating complete entity lists for export
  - Bulk processing operations that need all entities
  - Admin dashboards showing total entity counts
  - Search indexing (loading all entities for search)

PERFORMANCE CONSIDERATIONS:
  These functions return ALL entities without pagination or filtering:
  - For databases with many entities (10,000+ tracks), results can be large
  - Caching mitigates this - first call is expensive, subsequent calls are fast
  - Consider using filtered queries for large datasets
  - Memory usage scales with entity count

  Example sizes:
  - Small library (1,000 tracks): ~100KB result
  - Medium library (10,000 tracks): ~1MB result
  - Large library (100,000 tracks): ~10MB result

FILTERING AND PAGINATION:
  These functions do NOT support filtering or pagination:
  - They return ALL entities every time
  - For filtered results, use specific query functions:
    - get_scrobbles_of_artist() for artist-specific tracks
    - get_tracks_of_artist() for tracks by an artist
    - get_albums_of_artists() for albums by artists
  - For pagination, implement in the calling code

DEPENDENCIES:
  - core.schema: DB (table definitions)
  - core.connection: connection_provider decorator
  - crud.converters: Entity converters (artists_db_to_dict, etc.)
  - dbcache: cached_wrapper (result caching)
  - sqlalchemy: SQL operations

EXPORTED FUNCTIONS:
  - get_artists(dbconn=None) -> list[str]
      Get all artists (returns list of artist names)
  - get_tracks(dbconn=None) -> list[TrackDict]
      Get all tracks (returns list of track dictionaries)
  - get_albums(dbconn=None) -> list[AlbumDict]
      Get all albums (returns list of album dictionaries)

USAGE EXAMPLE:
  from maloja.database.queries import entities

  # Get all artists
  all_artists = entities.get_artists()
  # Returns: ['Artist A', 'Artist B', 'Artist C', ...]

  # Get all tracks
  all_tracks = entities.get_tracks()
  # Returns: [
  #   {'title': 'Song 1', 'artists': ['Artist A'], 'length': 180, ...},
  #   {'title': 'Song 2', 'artists': ['Artist B'], 'length': 240, ...},
  #   ...
  # ]

  # Get all albums
  all_albums = entities.get_albums()
  # Returns: [
  #   {'albumtitle': 'Album 1', 'artists': ['Artist A'], ...},
  #   {'albumtitle': 'Album 2', 'artists': ['Artist B'], ...},
  #   ...
  # ]

  # Example: Count entities
  artist_count = len(entities.get_artists())
  track_count = len(entities.get_tracks())
  album_count = len(entities.get_albums())
  print(f"Library: {artist_count} artists, {track_count} tracks, {album_count} albums")

  # Example: Populate UI dropdown
  artist_dropdown_options = entities.get_artists()
  for artist in artist_dropdown_options:
      print(f"<option>{artist}</option>")

CACHE INVALIDATION:
  When entities are modified, caches must be invalidated:
  - Adding/deleting artists → invalidate get_artists() cache
  - Adding/deleting tracks → invalidate get_tracks() cache
  - Adding/deleting albums → invalidate get_albums() cache
  - Editing entities → invalidate corresponding cache

  The wrapper layer (database/__init__.py) handles cache invalidation:
  - Calls dbcache.invalidate_entity_cache() after modifications
  - Ensures next get_artists/tracks/albums call returns fresh data

MIGRATION NOTES:
  This module was extracted from database/sqldb.py as part of Phase 5
  refactoring. Original location: sqldb.py lines 230-255 (entity listing
  functions).

SEE ALSO:
  - database/__init__.py: Wrapper functions that handle cache invalidation
  - database/sqldb.py: Re-exports for backward compatibility
  - crud/converters.py: Database row converters
  - queries/scrobbles.py: Scrobble retrieval queries
  - queries/relationships.py: Relationship queries (Phase 5)
  - docs/caching_strategy.md: Caching architecture
"""

from ..dbcache import cached_wrapper
from ..core.schema import DB
from ..core.connection import connection_provider
from ..crud.converters import artists_db_to_dict, tracks_db_to_dict, albums_db_to_dict


# ============================================================================
# ENTITY LISTING QUERIES
# ============================================================================

@cached_wrapper
@connection_provider
def get_artists(dbconn=None):
	"""
	Get all artists in the database.

	This returns a complete list of all artists, useful for populating
	dropdowns, generating exports, or bulk processing.

	Args:
		dbconn: Database connection (provided by decorator)

	Returns:
		List of artist names (strings)

	Performance:
		- First call: Queries database and converts all artist rows
		- Subsequent calls: Returns cached result (very fast)
		- Cache is invalidated when artists are added/modified/deleted

	Cache Invalidation:
		When artists are modified:
		- Adding new artist → cache invalidated
		- Editing artist name → cache invalidated
		- Deleting artist → cache invalidated
		- Merging artists → cache invalidated

	Example:
		# Get all artists
		artists = get_artists()
		print(f"Total artists: {len(artists)}")
		print(f"Artists: {', '.join(artists[:10])}...")

		# Populate UI dropdown
		for artist in get_artists():
			print(f"<option>{artist}</option>")
	"""

	op = DB['artists'].select()
	result = dbconn.execute(op).all()

	return artists_db_to_dict(result,dbconn=dbconn)

@cached_wrapper
@connection_provider
def get_tracks(dbconn=None):
	"""
	Get all tracks in the database.

	This returns a complete list of all tracks with full information
	(title, artists, length, album, etc.), useful for exports, bulk
	processing, or search indexing.

	Args:
		dbconn: Database connection (provided by decorator)

	Returns:
		List of TrackDict with full track information:
		- title: Track title (string)
		- artists: List of artist names (list[str])
		- length: Duration in seconds (int, optional)
		- album: Album info (AlbumDict, optional)

	Performance:
		- First call: Queries database, resolves all foreign keys
		- Subsequent calls: Returns cached result (very fast)
		- Cache is invalidated when tracks are added/modified/deleted
		- Large libraries (10,000+ tracks) may use significant memory

	Cache Invalidation:
		When tracks are modified:
		- Adding new track → cache invalidated
		- Editing track info → cache invalidated
		- Deleting track → cache invalidated
		- Modifying track-artist associations → cache invalidated
		- Modifying track-album associations → cache invalidated

	Example:
		# Get all tracks
		tracks = get_tracks()
		print(f"Total tracks: {len(tracks)}")

		# Export to JSON
		import json
		with open('tracks.json', 'w') as f:
			json.dump(tracks, f, indent=2)

		# Find longest tracks
		sorted_tracks = sorted(
			tracks,
			key=lambda t: t.get('length', 0),
			reverse=True
		)
		print(f"Longest track: {sorted_tracks[0]['title']} "
		      f"by {', '.join(sorted_tracks[0]['artists'])} "
		      f"({sorted_tracks[0]['length']}s)")
	"""

	op = DB['tracks'].select()
	result = dbconn.execute(op).all()

	return tracks_db_to_dict(result,dbconn=dbconn)

@cached_wrapper
@connection_provider
def get_albums(dbconn=None):
	"""
	Get all albums in the database.

	This returns a complete list of all albums with full information
	(album title, artists), useful for exports, bulk processing, or
	admin dashboards.

	Args:
		dbconn: Database connection (provided by decorator)

	Returns:
		List of AlbumDict with full album information:
		- albumtitle: Album title (string)
		- artists: List of artist names (list[str])

	Performance:
		- First call: Queries database, resolves all foreign keys
		- Subsequent calls: Returns cached result (very fast)
		- Cache is invalidated when albums are added/modified/deleted

	Cache Invalidation:
		When albums are modified:
		- Adding new album → cache invalidated
		- Editing album info → cache invalidated
		- Deleting album → cache invalidated
		- Modifying album-artist associations → cache invalidated

	Example:
		# Get all albums
		albums = get_albums()
		print(f"Total albums: {len(albums)}")

		# Group by artist
		from collections import defaultdict
		albums_by_artist = defaultdict(list)
		for album in albums:
			for artist in album['artists']:
				albums_by_artist[artist].append(album['albumtitle'])

		# Print discography for top artist
		top_artist = max(albums_by_artist.items(), key=lambda x: len(x[1]))
		print(f"{top_artist[0]}: {len(top_artist[1])} albums")
		print(f"  Albums: {', '.join(sorted(top_artist[1]))}")
	"""

	op = DB['albums'].select()
	result = dbconn.execute(op).all()

	return albums_db_to_dict(result,dbconn=dbconn)
