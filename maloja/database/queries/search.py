"""
Search Query Operations
========================

This module contains fuzzy search functionality for finding entities by partial name/title.

ARCHITECTURE:
  This is part of the queries layer in the database module hierarchy:
  - core/ (schema, connection, types, IDs, cache)
  - crud/ (scrobbles, tracks, artists, albums, associations)
  - queries/ (scrobbles, entities, relationships, search) ← YOU ARE HERE
  - charts/ (specialized chart operations)

SEARCH FUNCTIONALITY:
  This module provides fuzzy search functions to find entities by partial matches:

  1. search_artist(searchterm)
     - Search artists by name (case-insensitive, fuzzy matching)
     - Returns matching artist names

  2. search_track(searchterm)
     - Search tracks by title (case-insensitive, fuzzy matching)
     - Returns matching track dictionaries

  3. search_album(searchterm)
     - Search albums by title (case-insensitive, fuzzy matching)
     - Returns matching album dictionaries

NAME NORMALIZATION:
  All searches use normalized names/titles for fuzzy matching:
  - Artist names are normalized: "The Beatles" → "beatles"
  - Track titles are normalized: "Hey Jude!" → "hey jude"
  - Album titles are normalized: "Abbey Road (Remastered)" → "abbey road remastered"

  Normalization process (via utils.helpers.normalize_name):
  - Convert to lowercase
  - Remove leading/trailing whitespace
  - Remove punctuation/special characters
  - Collapse multiple spaces to single space

  This allows fuzzy matching:
  - "beatles" matches "The Beatles", "Beatles", "BEATLES"
  - "abbey" matches "Abbey Road", "abbey road", "ABBEY ROAD"

CASE-INSENSITIVE MATCHING:
  Searches use SQL ILIKE (case-insensitive LIKE) with wildcard patterns:
  - Pattern: f"%{normalize_name(searchterm)}%"
  - Example: searchterm="beat" → pattern="%beat%"
  - Matches: "Beatles", "Heartbeat", "Beat It"

  The normalized columns in the database:
  - artists.name_normalized: Normalized artist name
  - tracks.title_normalized: Normalized track title
  - albums.albtitle_normalized: Normalized album title

WILDCARD MATCHING:
  All searches use % wildcards (SQL LIKE wildcards):
  - Leading %: Matches if searchterm appears anywhere in name/title
  - Trailing %: Matches if searchterm appears anywhere in name/title
  - No anchoring: Partial matches anywhere in the string

  Examples:
  - search_artist("beat") matches:
    - "The Beatles" (contains "beat")
    - "Heartbeat" (contains "beat")
    - "Beat Crusaders" (starts with "beat")

  - search_track("love") matches:
    - "Love Song"
    - "I Love You"
    - "Lovely Day"
    - "Unloved"

CACHING:
  All functions are decorated with @cached_wrapper for performance:
  - Results are cached based on searchterm
  - Cache is invalidated when database changes (via dbcache)
  - First search for "beat" is slow, subsequent searches for "beat" are fast
  - Different search terms have separate cache entries

RESULT FORMAT:
  Each search function returns different result types:

  search_artist:
  - Returns: list[str]
  - Example: ['The Beatles', 'Beatles For Sale', 'Beat Crusaders']

  search_track:
  - Returns: list[TrackDict]
  - Example: [
      {'title': 'Hey Jude', 'artists': ['The Beatles'], 'length': 431, ...},
      {'title': 'Let It Be', 'artists': ['The Beatles'], 'length': 243, ...}
    ]

  search_album:
  - Returns: list[AlbumDict]
  - Example: [
      {'albumtitle': 'Abbey Road', 'artists': ['The Beatles']},
      {'albumtitle': 'Let It Be', 'artists': ['The Beatles']}
    ]

PERFORMANCE CONSIDERATIONS:
  Fuzzy searches use ILIKE which is slower than exact matches:
  - ILIKE scans the entire normalized column
  - No index acceleration for LIKE '%term%' patterns
  - Performance scales with database size (O(n) where n = entity count)

  For large databases (100,000+ entities):
  - First search: May take 50-100ms
  - Cached search: <1ms
  - Consider limiting search term length for performance

ENTITY RESOLUTION:
  Search functions call get_artist/get_track/get_album from queries.relationships
  to resolve entity IDs to full entity data. This ensures:
  - Consistent entity format across all query functions
  - Proper foreign key resolution (track artists, album artists)
  - Reuse of cached entity data

DEPENDENCIES:
  - core.schema: DB (table definitions with normalized columns)
  - core.connection: connection_provider decorator
  - utils.helpers: normalize_name (string normalization)
  - queries.relationships: get_artist, get_track, get_album (entity resolution)
  - dbcache: cached_wrapper (result caching)
  - sqlalchemy: SQL operations

EXPORTED FUNCTIONS:
  - search_artist(searchterm, dbconn=None) -> list[str]
      Search artists by name (fuzzy matching)
  - search_track(searchterm, dbconn=None) -> list[TrackDict]
      Search tracks by title (fuzzy matching)
  - search_album(searchterm, dbconn=None) -> list[AlbumDict]
      Search albums by title (fuzzy matching)

USAGE EXAMPLES:

  from maloja.database.queries import search

  # Search for artists
  beatles_artists = search.search_artist("beat")
  # Returns: ['The Beatles', 'Beat Crusaders', 'Heartbeat']

  # Search for tracks
  love_tracks = search.search_track("love")
  # Returns: [
  #   {'title': 'Love Song', 'artists': ['Artist A'], ...},
  #   {'title': 'I Love You', 'artists': ['Artist B'], ...},
  #   ...
  # ]

  # Search for albums
  live_albums = search.search_album("live")
  # Returns: [
  #   {'albumtitle': 'Live at Budokan', 'artists': ['Artist A']},
  #   {'albumtitle': 'Liverpool', 'artists': ['Artist B']},
  #   ...
  # ]

  # Case-insensitive search
  results = search.search_artist("BEATLES")
  # Same as: search.search_artist("beatles")
  # Same as: search.search_artist("BeAtLeS")

  # Partial matching
  results = search.search_track("jude")
  # Matches: "Hey Jude", "St. Jude", "Jude's Theme"

MIGRATION NOTES:
  This module was extracted from database/sqldb.py as part of Phase 5
  refactoring. Original location: sqldb.py lines 1807-1835 (search
  functions).

  Entity resolution imports from queries.relationships:
  - get_artist, get_track, get_album moved to queries.relationships in Task 5.3
  - Search functions now import from queries.relationships instead of local

SEE ALSO:
  - database/__init__.py: Wrapper functions that handle cache invalidation
  - database/sqldb.py: Re-exports for backward compatibility
  - queries/relationships.py: get_artist, get_track, get_album (entity resolution)
  - utils/helpers.py: normalize_name() function
  - docs/search_strategy.md: Search architecture and performance
"""

from ..dbcache import cached_wrapper
from ..core.schema import DB
from ..core.connection import connection_provider
from ..utils.helpers import normalize_name
from .relationships import get_artist, get_track, get_album


# ============================================================================
# SEARCH QUERIES
# ============================================================================

@cached_wrapper
@connection_provider
def search_artist(searchterm,dbconn=None):
	"""
	Search artists by name (case-insensitive fuzzy matching).

	This performs a fuzzy search on normalized artist names using SQL ILIKE
	with wildcard patterns. The search is case-insensitive and matches
	partial names.

	Args:
		searchterm: Search string (will be normalized for matching)
		dbconn: Database connection (provided by decorator)

	Returns:
		List of matching artist names (strings)

	Normalization:
		Both the searchterm and artist names are normalized:
		- Lowercase conversion
		- Whitespace trimming
		- Special character removal
		- Multiple space collapse

	Wildcard Matching:
		Uses pattern: "%{normalized_searchterm}%"
		- Matches searchterm anywhere in artist name
		- No anchoring (can match at start, middle, or end)

	Example:
		# Search for "beatles"
		artists = search_artist("beat")
		# Returns: ['The Beatles', 'Beat Crusaders', 'Heartbeat']

		# Case-insensitive
		artists = search_artist("BEATLES")
		# Same results as: search_artist("beatles")

		# Partial matching
		artists = search_artist("beat crus")
		# Returns: ['Beat Crusaders']
	"""
	op = DB['artists'].select().where(
		DB['artists'].c.name_normalized.ilike(normalize_name(f"%{searchterm}%"))
	)
	result = dbconn.execute(op).all()

	return [get_artist(row.id,dbconn=dbconn) for row in result]

@cached_wrapper
@connection_provider
def search_track(searchterm,dbconn=None):
	"""
	Search tracks by title (case-insensitive fuzzy matching).

	This performs a fuzzy search on normalized track titles using SQL ILIKE
	with wildcard patterns. The search is case-insensitive and matches
	partial titles.

	Args:
		searchterm: Search string (will be normalized for matching)
		dbconn: Database connection (provided by decorator)

	Returns:
		List of matching TrackDict with full track information:
		- title: Track title (string)
		- artists: List of artist names (list[str])
		- length: Duration in seconds (int, optional)
		- album: Album info (AlbumDict, optional)

	Normalization:
		Both the searchterm and track titles are normalized:
		- Lowercase conversion
		- Whitespace trimming
		- Special character removal
		- Multiple space collapse

	Wildcard Matching:
		Uses pattern: "%{normalized_searchterm}%"
		- Matches searchterm anywhere in track title
		- No anchoring (can match at start, middle, or end)

	Example:
		# Search for "love"
		tracks = search_track("love")
		# Returns: [
		#   {'title': 'Love Song', 'artists': ['Artist A'], ...},
		#   {'title': 'I Love You', 'artists': ['Artist B'], ...},
		#   {'title': 'Lovely Day', 'artists': ['Artist C'], ...}
		# ]

		# Case-insensitive
		tracks = search_track("JUDE")
		# Same results as: search_track("jude")

		# Partial matching
		tracks = search_track("hey")
		# Returns tracks with "hey" in title: "Hey Jude", "Hey Ya", etc.
	"""
	op = DB['tracks'].select().where(
		DB['tracks'].c.title_normalized.ilike(normalize_name(f"%{searchterm}%"))
	)
	result = dbconn.execute(op).all()

	return [get_track(row.id,dbconn=dbconn) for row in result]

@cached_wrapper
@connection_provider
def search_album(searchterm,dbconn=None):
	"""
	Search albums by title (case-insensitive fuzzy matching).

	This performs a fuzzy search on normalized album titles using SQL ILIKE
	with wildcard patterns. The search is case-insensitive and matches
	partial titles.

	Args:
		searchterm: Search string (will be normalized for matching)
		dbconn: Database connection (provided by decorator)

	Returns:
		List of matching AlbumDict with full album information:
		- albumtitle: Album title (string)
		- artists: List of artist names (list[str])

	Normalization:
		Both the searchterm and album titles are normalized:
		- Lowercase conversion
		- Whitespace trimming
		- Special character removal
		- Multiple space collapse

	Wildcard Matching:
		Uses pattern: "%{normalized_searchterm}%"
		- Matches searchterm anywhere in album title
		- No anchoring (can match at start, middle, or end)

	Example:
		# Search for "live"
		albums = search_album("live")
		# Returns: [
		#   {'albumtitle': 'Live at Budokan', 'artists': ['Artist A']},
		#   {'albumtitle': 'Liverpool', 'artists': ['Artist B']},
		#   {'albumtitle': 'Alive', 'artists': ['Artist C']}
		# ]

		# Case-insensitive
		albums = search_album("ROAD")
		# Same results as: search_album("road")

		# Partial matching
		albums = search_album("abbey")
		# Returns albums with "abbey" in title: "Abbey Road", etc.
	"""
	op = DB['albums'].select().where(
		DB['albums'].c.albtitle_normalized.ilike(normalize_name(f"%{searchterm}%"))
	)
	result = dbconn.execute(op).all()

	return [get_album(row.id,dbconn=dbconn) for row in result]
