"""
Top Entity Query Implementations (UNDECORATED)

This module contains the implementation functions for top entity queries.
The @cached_wrapper decorators remain in database/sqldb.py to preserve cache
identity (Decision 6 - Cache Function Identity Preservation).

Each function here is named with an _impl suffix and is called by a thin wrapper
in sqldb.py that has the @cached_wrapper decorator.

Functions:
- get_top_entities_direct_impl: Generic top entities query with optimizations
- get_top_artists_direct_impl: Top artists convenience wrapper
- get_top_tracks_direct_impl: Top tracks convenience wrapper
- get_top_albums_direct_impl: Top albums convenience wrapper
"""

import sqlalchemy as sql

from ..core.connection import connection_provider
from ..queries.relationships import get_tracks_map, get_artists_map, get_albums_map
from ..utils.helpers import rank

# Generic track titles to exclude from rankings (normalized lowercase)
# These are placeholder titles that pollute rankings when albums have
# multiple tracks with identical generic titles (e.g., experimental albums)
EXCLUDED_TRACK_TITLES = {
	'(untitled)',
	'untitled',
	'-',
	'—',  # em dash
	'–',  # en dash
	'',   # empty string
}


@connection_provider
def get_top_entities_direct_impl(entity_type, since, to=None, associated=True, resolve_ids=True, limit=None, dbconn=None):
	"""
	Query scrobbles directly for top entities in time range.

	Fast indexed query using appropriate indexes per entity type.
	Replaces get_top_artists_direct, get_top_tracks_direct, get_top_albums_direct.

	Args:
		entity_type: 'artist', 'track', or 'album'
		since: Start timestamp
		to: End timestamp (None for all time / no upper bound)
		associated: Include associated artists (only applies to artists, merged into main artist)
		resolve_ids: Include entity names/info in result
		limit: Maximum number of results to return (None for unlimited)
		dbconn: Database connection

	Returns:
		List of dicts with scrobbles, {entity}_id, rank (and entity info if resolve_ids=True)
		For artists also includes real_scrobbles (non-merged count)
	"""

	# Build WHERE clause based on time range
	if to is None:
		time_filter = "s.timestamp >= :since"
		params = {"since": since}
	else:
		time_filter = "s.timestamp >= :since AND s.timestamp < :to"
		params = {"since": since, "to": to}

	# Configuration for different entity types
	if entity_type == 'artist':
		id_col = 'artist_id'
		resolver = get_artists_map
		entity_key = 'artist'

		if associated:
			# Query with associated artist merging
			query = sql.text(f"""
				WITH artist_counts AS (
					SELECT
						COALESCE(aa.target_artist, ta.artist_id) as artist_id,
						COUNT(*) as scrobbles,
						SUM(CASE WHEN aa.target_artist IS NULL THEN 1 ELSE 0 END) as real_scrobbles
					FROM scrobbles s
					JOIN trackartists ta ON s.track_id = ta.track_id
					LEFT JOIN associated_artists aa ON ta.artist_id = aa.source_artist
					WHERE {time_filter}
					GROUP BY COALESCE(aa.target_artist, ta.artist_id)
				)
				SELECT artist_id, scrobbles, real_scrobbles
				FROM artist_counts
				ORDER BY scrobbles DESC, real_scrobbles DESC
			""")
		else:
			# Query without associated artists
			query = sql.text(f"""
				SELECT
					ta.artist_id,
					COUNT(*) as scrobbles,
					COUNT(*) as real_scrobbles
				FROM scrobbles s
				JOIN trackartists ta ON s.track_id = ta.track_id
				WHERE {time_filter}
				GROUP BY ta.artist_id
				ORDER BY scrobbles DESC
			""")

	elif entity_type == 'track':
		id_col = 'track_id'
		resolver = get_tracks_map
		entity_key = 'track'

		# Build placeholders for excluded titles and add to params
		excluded_list = list(EXCLUDED_TRACK_TITLES)
		placeholders = ', '.join(f':excluded_{i}' for i in range(len(excluded_list)))
		for i, title in enumerate(excluded_list):
			params[f'excluded_{i}'] = title

		query = sql.text(f"""
			SELECT
				s.track_id,
				COUNT(*) as scrobbles
			FROM scrobbles s
			JOIN tracks t ON s.track_id = t.id
			WHERE {time_filter}
			  AND t.title_normalized NOT IN ({placeholders})
			GROUP BY s.track_id
			ORDER BY scrobbles DESC
		""")

	elif entity_type == 'album':
		id_col = 'album_id'
		resolver = get_albums_map
		entity_key = 'album'

		query = sql.text(f"""
			SELECT
				t.album_id,
				COUNT(*) as scrobbles
			FROM scrobbles s
			JOIN tracks t ON s.track_id = t.id
			WHERE {time_filter}
			  AND t.album_id IS NOT NULL
			GROUP BY t.album_id
			ORDER BY scrobbles DESC
		""")

	else:
		raise ValueError(f"Invalid entity_type: {entity_type}")

	# Execute query
	result = dbconn.execute(query, params).all()

	# Resolve IDs to entity info if requested
	if resolve_ids:
		entities = resolver([getattr(row, id_col) for row in result], dbconn=dbconn)
		if entity_type == 'artist':
			result = [{
				'scrobbles': row.scrobbles,
				'real_scrobbles': row.real_scrobbles,
				entity_key: entities[getattr(row, id_col)],
				id_col: getattr(row, id_col)
			} for row in result]
		else:
			result = [{
				'scrobbles': row.scrobbles,
				entity_key: entities[getattr(row, id_col)],
				id_col: getattr(row, id_col)
			} for row in result]
	else:
		if entity_type == 'artist':
			result = [{
				'scrobbles': row.scrobbles,
				'real_scrobbles': row.real_scrobbles,
				id_col: getattr(row, id_col)
			} for row in result]
		else:
			result = [{
				'scrobbles': row.scrobbles,
				id_col: getattr(row, id_col)
			} for row in result]

	result = rank(result, key='scrobbles')

	# Apply limit if specified
	if limit is not None:
		result = result[:limit]

	return result


@connection_provider
def get_top_artists_direct_impl(since, to=None, associated=True, resolve_ids=True, limit=None, dbconn=None):
	"""
	Query scrobbles directly for top artists.

	Convenience wrapper around get_top_entities_direct_impl for artists.

	Args:
		since: Start timestamp
		to: End timestamp (None for all time)
		associated: Include associated/merged artists
		resolve_ids: Include artist names in result
		limit: Maximum number of results (None for unlimited)
		dbconn: Database connection

	Returns:
		List of dicts with scrobbles, real_scrobbles, artist_id (and artist if resolve_ids=True)
	"""
	return get_top_entities_direct_impl('artist', since, to, associated=associated, resolve_ids=resolve_ids, limit=limit, dbconn=dbconn)


@connection_provider
def get_top_tracks_direct_impl(since, to=None, resolve_ids=True, limit=None, dbconn=None):
	"""
	Query scrobbles directly for top tracks.

	Convenience wrapper around get_top_entities_direct_impl for tracks.

	Args:
		since: Start timestamp
		to: End timestamp (None for all time)
		resolve_ids: Include track info in result
		limit: Maximum number of results (None for unlimited)
		dbconn: Database connection

	Returns:
		List of dicts with scrobbles, track_id (and track if resolve_ids=True)
	"""
	return get_top_entities_direct_impl('track', since, to, associated=False, resolve_ids=resolve_ids, limit=limit, dbconn=dbconn)


@connection_provider
def get_top_albums_direct_impl(since, to=None, resolve_ids=True, limit=None, dbconn=None):
	"""
	Query scrobbles directly for top albums.

	Convenience wrapper around get_top_entities_direct_impl for albums.

	Args:
		since: Start timestamp
		to: End timestamp (None for all time)
		resolve_ids: Include album info in result
		limit: Maximum number of results (None for unlimited)
		dbconn: Database connection

	Returns:
		List of dicts with scrobbles, album_id (and album if resolve_ids=True)
	"""
	return get_top_entities_direct_impl('album', since, to, associated=False, resolve_ids=resolve_ids, limit=limit, dbconn=dbconn)
