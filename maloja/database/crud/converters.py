"""
Database entity converters between DB rows and Python dictionaries.

This module provides bidirectional conversion functions:
- DB → Dict: Convert database rows to typed dictionaries
- Dict → DB: Convert typed dictionaries to database column values

The converters are used throughout the database layer to maintain
a consistent API and separate database schema from application logic.

TYPE DEFINITIONS:
-----------------
TypedDict classes (ScrobbleDict, TrackDict, AlbumDict) are imported from
core/types.py. This follows the layered architecture where types are
foundational (core layer) and conversions build on top of them (crud layer).
"""

from typing import cast
import json

from ..core.types import ScrobbleDict, TrackDict, AlbumDict


##### DB → DICT CONVERTERS

def scrobbles_db_to_dict(rows, include_internal=False, dbconn=None) -> list[ScrobbleDict]:
	"""
	Convert database rows to ScrobbleDict list.

	Efficiently batches track lookups to avoid N+1 queries.

	Args:
		rows: Database rows from scrobbles table
		include_internal: Include 'extra' and 'rawscrobble' fields
		dbconn: Database connection (passed through to helper functions)

	Returns:
		List of ScrobbleDict objects with full track information
	"""
	# Import get_tracks_map from queries module
	# Phase 5: Moved to queries/relationships.py, no longer in sqldb
	from ..queries.relationships import get_tracks_map

	tracks: list[TrackDict] = get_tracks_map(set(row.track_id for row in rows), dbconn=dbconn)
	return [
		cast(ScrobbleDict, {
			**{
				"time": row.timestamp,
				"track": tracks[row.track_id],
				"duration": row.duration,
				"origin": row.origin
			},
			**({
				"extra": json.loads(row.extra or '{}'),
				"rawscrobble": json.loads(row.rawscrobble or '{}')
			} if include_internal else {})
		})

		for row in rows
	]


def scrobble_db_to_dict(row, dbconn=None) -> ScrobbleDict:
	"""Convert single database row to ScrobbleDict."""
	return scrobbles_db_to_dict([row], dbconn=dbconn)[0]


def tracks_db_to_dict(rows, dbconn=None) -> list[TrackDict]:
	"""
	Convert database rows to TrackDict list.

	Efficiently batches artist and album lookups.

	Args:
		rows: Database rows from tracks table
		dbconn: Database connection

	Returns:
		List of TrackDict objects with artist and album information
	"""
	# Import from queries module
	# Phase 5: Moved to queries/relationships.py, no longer in sqldb
	from ..queries.relationships import get_artists_of_tracks, get_albums_map

	artists = get_artists_of_tracks(set(row.id for row in rows), dbconn=dbconn)
	albums = get_albums_map(set(row.album_id for row in rows), dbconn=dbconn)

	result = [
		cast(TrackDict, {
			"artists":artists[row.id],
			"title":row.title,
			"album":albums.get(row.album_id),
			"length":row.length,
			"track_id":row.id  # Include track_id to avoid lookups in image resolution
		})
		for row in rows
	]
	return result


def track_db_to_dict(row, dbconn=None) -> TrackDict:
	"""Convert single database row to TrackDict."""
	return tracks_db_to_dict([row], dbconn=dbconn)[0]


def artists_db_to_dict(rows, dbconn=None) -> list[str]:
	"""
	Convert database rows to artist name list.

	Args:
		rows: Database rows from artists table
		dbconn: Database connection (unused, for consistency)

	Returns:
		List of artist names
	"""
	return [
		row.name
		for row in rows
	]


def artist_db_to_dict(row, dbconn=None) -> str:
	"""Convert single database row to artist name."""
	return artists_db_to_dict([row], dbconn=dbconn)[0]


def albums_db_to_dict(rows, dbconn=None) -> list[AlbumDict]:
	"""
	Convert database rows to AlbumDict list.

	Efficiently batches artist lookups.

	Args:
		rows: Database rows from albums table
		dbconn: Database connection

	Returns:
		List of AlbumDict objects with artist information
	"""
	# Import get_artists_of_albums from queries module
	# Phase 5: Moved to queries/relationships.py, no longer in sqldb
	from ..queries.relationships import get_artists_of_albums

	artists = get_artists_of_albums(set(row.id for row in rows), dbconn=dbconn)
	return [
		cast(AlbumDict, {
			"artists": artists.get(row.id),
			"albumtitle": row.albtitle,
			"album_id": row.id  # Include album_id to avoid lookups in image resolution
		})
		for row in rows
	]


def album_db_to_dict(row, dbconn=None) -> AlbumDict:
	"""Convert single database row to AlbumDict."""
	return albums_db_to_dict([row], dbconn=dbconn)[0]


##### DICT → DB CONVERTERS
# These return None for missing fields so they can be used in UPDATE statements

def scrobble_dict_to_db(info: ScrobbleDict, update_album=False, dbconn=None):
	"""
	Convert ScrobbleDict to database column values.

	Args:
		info: Scrobble dictionary from API/import
		update_album: Whether to update album associations
		dbconn: Database connection

	Returns:
		Dictionary of database column names to values
	"""
	# Import here to avoid circular dependency during module initialization
	from ..core.ids import get_track_id

	return {
		"timestamp": info.get('time'),
		"origin": info.get('origin'),
		"duration": info.get('duration'),
		"track_id": get_track_id(info.get('track'), update_album=update_album, dbconn=dbconn),
		"extra": json.dumps(info.get('extra')) if info.get('extra') else None,
		"rawscrobble": json.dumps(info.get('rawscrobble')) if info.get('rawscrobble') else None
	}


def track_dict_to_db(info: TrackDict, dbconn=None):
	"""
	Convert TrackDict to database column values.

	Args:
		info: Track dictionary
		dbconn: Database connection (unused, for consistency)

	Returns:
		Dictionary of database column names to values
	"""
	# Import here to avoid circular dependency during module initialization
	from ..utils.helpers import normalize_name

	return {
		"title": info.get('title'),
		"title_normalized": normalize_name(info.get('title', '')) or None,
		"length": info.get('length')
	}


def artist_dict_to_db(info: str, dbconn=None):
	"""
	Convert artist name to database column values.

	Args:
		info: Artist name
		dbconn: Database connection (unused, for consistency)

	Returns:
		Dictionary of database column names to values
	"""
	# Import here to avoid circular dependency during module initialization
	from ..utils.helpers import normalize_name

	return {
		"name": info,
		"name_normalized": normalize_name(info)
	}


def album_dict_to_db(info: AlbumDict, dbconn=None):
	"""
	Convert AlbumDict to database column values.

	Args:
		info: Album dictionary
		dbconn: Database connection (unused, for consistency)

	Returns:
		Dictionary of database column names to values
	"""
	# Import here to avoid circular dependency during module initialization
	from ..utils.helpers import normalize_name

	return {
		"albtitle": info.get('albumtitle'),
		"albtitle_normalized": normalize_name(info.get('albumtitle'))
	}
