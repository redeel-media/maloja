"""
Database Type Definitions
==========================

This module defines the core TypedDict classes used throughout the database layer
to represent entities (scrobbles, tracks, artists, albums) in Python dictionaries.

TYPE DEFINITIONS:
-----------------

The database layer uses TypedDict classes to provide type safety and IDE support
for dictionary-based data structures. These types represent the schema of data
as it flows through the application:

1. **ScrobbleDict**: Individual play event
   - Required: time, track, duration, origin
   - Optional: extra, rawscrobble (internal metadata)

2. **TrackDict**: Track metadata
   - Required: artists, title
   - Optional: album, length, track_id

3. **AlbumDict**: Album metadata
   - Required: albumtitle, artists
   - Optional: album_id

TYPE USAGE:
-----------

These types are used at multiple layers:

- **API Layer**: Incoming scrobbles from external sources
- **Database Layer**: Query results converted to dicts
- **Template Layer**: Rendering artist pages, charts, etc.
- **Business Logic**: Aggregations, statistics, merging

DESIGN DECISIONS:
-----------------

1. **total=False for Track/Album Dicts**
   - Allows partial dictionaries during construction
   - Fields can be populated incrementally
   - ID fields (track_id, album_id) are optional (only present after DB fetch)

2. **total=True for ScrobbleDict (default)**
   - All fields required for data integrity
   - Scrobbles are immutable once created
   - Missing fields indicate data quality issues

3. **Nested Structure**
   - ScrobbleDict contains TrackDict
   - TrackDict optionally contains AlbumDict
   - Matches JSON API structure for external clients

4. **ID Fields Optional**
   - track_id, album_id only included when fetched from database
   - Avoids circular lookups during construction
   - Enables caching and optimization

IMPORTANT NOTES:
----------------

1. **These are Type Definitions Only**
   - No conversion logic in this module
   - No database queries or ID resolution
   - Pure type specifications

2. **Conversion Functions Elsewhere**
   - DB→Dict conversions: crud/converters.py
   - Dict→DB conversions: crud/converters.py
   - ID resolution: core/ids.py (future)

3. **Backward Compatibility**
   - These types mirror the exact structure used historically
   - Changing these types affects the entire codebase
   - Any changes must maintain API compatibility

MIGRATION FROM crud/converters.py:
----------------------------------

These TypedDict classes were previously defined in crud/converters.py alongside
conversion functions. They have been moved to core/types.py to:

- Establish types as foundational (core layer)
- Allow crud/ and queries/ to both depend on types
- Follow the layered architecture (core → crud → queries)
- Prevent circular dependencies in future refactoring

DO NOT:
-------
- Add database queries to this module
- Add conversion functions requiring ID resolution
- Import from crud/, queries/, or charts/ layers
- Modify type structures without checking all usages

DEPENDENCIES:
-------------

This module has ZERO dependencies on other database modules:
- No imports from crud/
- No imports from queries/
- No imports from sqldb
- Only standard library imports (typing)

This keeps types truly foundational and prevents circular imports.
"""

from typing import TypedDict


# ============================================================================
# TYPE DEFINITIONS
# ============================================================================

class AlbumDict(TypedDict, total=False):
	"""
	Album metadata dictionary.

	This represents an album in the Maloja database. Used throughout the
	codebase for album information in API responses, templates, and
	internal processing.

	Fields:
		albumtitle: Album title (e.g., "Dark Side of the Moon")
		artists: List of artist names (e.g., ["Pink Floyd"])
		album_id: Database ID (optional, only present after DB fetch)

	Usage:
		# Construction (API input):
		album = AlbumDict(albumtitle="Thriller", artists=["Michael Jackson"])

		# From database (includes album_id):
		album = AlbumDict(albumtitle="Thriller", artists=["Michael Jackson"], album_id=42)

	Notes:
		- total=False allows incremental construction
		- album_id is optional (only for database results)
		- artists is a list even for single-artist albums
	"""
	albumtitle: str
	artists: list[str]
	album_id: int  # Optional: included when album is fetched from DB


class TrackDict(TypedDict, total=False):
	"""
	Track metadata dictionary.

	This represents a track (song) in the Maloja database. Used throughout
	the codebase for track information in API responses, scrobbles, and
	internal processing.

	Fields:
		artists: List of artist names (e.g., ["The Beatles", "Eric Clapton"])
		title: Track title (e.g., "While My Guitar Gently Weeps")
		album: Album information (optional, see AlbumDict)
		length: Track duration in seconds (optional, None if unknown)
		track_id: Database ID (optional, only present after DB fetch)

	Usage:
		# Minimal construction:
		track = TrackDict(artists=["Queen"], title="Bohemian Rhapsody")

		# With album:
		track = TrackDict(
		    artists=["Queen"],
		    title="Bohemian Rhapsody",
		    album=AlbumDict(albumtitle="A Night at the Opera", artists=["Queen"])
		)

		# From database (includes track_id):
		track = TrackDict(..., track_id=123)

	Notes:
		- total=False allows partial dictionaries
		- album field is optional (not all scrobbles include album)
		- length can be None if duration is unknown
		- track_id only present for database-fetched tracks
	"""
	artists: list[str]
	title: str
	album: AlbumDict
	length: int | None
	track_id: int  # Optional: included when track is fetched from DB


class ScrobbleDict(TypedDict):
	"""
	Scrobble (play event) dictionary.

	This represents a single play event in the Maloja database. This is the
	core data structure for the scrobbling system.

	Required Fields:
		time: Unix timestamp of the scrobble (seconds since epoch)
		track: Track information (see TrackDict)
		duration: How long the track was played (seconds)
		origin: Source of the scrobble (e.g., "maloja-web", "lastfm-import")

	Optional Internal Fields:
		extra: Additional metadata (JSON dict)
		rawscrobble: Original scrobble data before normalization (JSON dict)

	Usage:
		# Typical scrobble from API:
		scrobble = ScrobbleDict(
		    time=1699123456,
		    track=TrackDict(artists=["Radiohead"], title="Paranoid Android"),
		    duration=383,
		    origin="spotify-connector"
		)

		# Internal scrobble (with metadata):
		scrobble = ScrobbleDict(
		    time=1699123456,
		    track=...,
		    duration=383,
		    origin="maloja-web",
		    extra={"ip": "127.0.0.1", "user_agent": "..."},
		    rawscrobble={"original": "data"}
		)

	Notes:
		- total=True (default) makes all fields required
		- time is Unix timestamp (not datetime object)
		- duration is actual listen time (may differ from track length)
		- origin identifies the scrobble source for debugging
		- extra and rawscrobble are for internal use only
	"""
	time: int
	track: TrackDict
	duration: int
	origin: str
	# Optional internal fields:
	extra: dict
	rawscrobble: dict
