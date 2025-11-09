"""
Count Aggregation Implementations (UNDECORATED)

This module contains the implementation functions for count aggregation queries.
The @cached_wrapper decorators remain in database/sqldb.py to preserve cache
identity (Decision 6 - Cache Function Identity Preservation).

Each function here is named with an _impl suffix and is called by a thin wrapper
in sqldb.py that has the @cached_wrapper decorator.

Functions:
- count_scrobbles_by_artist_impl: Count scrobbles grouped by artist
- count_scrobbles_by_track_impl: Count scrobbles grouped by track
- count_scrobbles_by_album_impl: Count scrobbles grouped by album
- count_scrobbles_by_album_combined_impl: Count scrobbles by albums artist appears on
- count_scrobbles_by_album_of_artist_impl: Count scrobbles by artist's albums
- count_scrobbles_of_artist_by_album_impl: Count artist's track scrobbles grouped by album
- count_scrobbles_by_track_of_artist_impl: Count scrobbles by artist's tracks
- count_scrobbles_by_track_of_album_impl: Count scrobbles by album's tracks
"""

import sqlalchemy as sql

from ..core.schema import DB
from ..core.connection import connection_provider
from ..core.ids import get_artist_id, get_album_id
from ..core.cache import ensure_request_cache
from ..queries.relationships import (
	get_tracks_map, get_artists_map, get_albums_map,
	get_associated_artists
)
from ..utils.helpers import rank


@connection_provider
def count_scrobbles_by_artist_impl(since, to, associated=True, resolve_ids=True, dbconn=None):
	"""
	Count scrobbles grouped by artist.

	Args:
		since: Start timestamp
		to: End timestamp
		associated: Include associated/merged artists
		resolve_ids: Include artist names in result
		dbconn: Database connection

	Returns:
		List of dicts with scrobbles, real_scrobbles, artist_id (and artist if resolve_ids=True)
	"""
	jointable = sql.join(
		DB['scrobbles'],
		DB['trackartists'],
		DB['scrobbles'].c.track_id == DB['trackartists'].c.track_id
	)

	jointable2 = sql.join(
		jointable,
		DB['associated_artists'],
		DB['trackartists'].c.artist_id == DB['associated_artists'].c.source_artist,
		isouter=True
	)

	if associated:
		artistselect = sql.func.coalesce(DB['associated_artists'].c.target_artist, DB['trackartists'].c.artist_id)
	else:
		artistselect = DB['trackartists'].c.artist_id

	op = sql.select(
		sql.func.count(sql.func.distinct(DB['scrobbles'].c.timestamp)).label('count'),
		# only count distinct scrobbles - because of artist replacement, we could end up
		# with two artists of the same scrobble counting it twice for the same artist
		# e.g. Irene and Seulgi adding two scrobbles to Red Velvet for one real scrobble
		artistselect.label('artist_id'),
		# use the replaced artist as artist to count if it exists, otherwise original one
		sql.func.sum(
			sql.case((DB['trackartists'].c.artist_id == artistselect, 1), else_=0)
		).label('really_by_this_artist')
		# also select the original artist in any case as a separate column
	).select_from(jointable2).where(
		DB['scrobbles'].c.timestamp.between(since, to)
	).group_by(
		artistselect
	).order_by(sql.desc('count'), sql.desc('really_by_this_artist'))
	result = dbconn.execute(op).all()

	if resolve_ids:
		artists = get_artists_map([row.artist_id for row in result], dbconn=dbconn)
		result = [{'scrobbles': row.count, 'real_scrobbles': row.really_by_this_artist, 'artist': artists[row.artist_id], 'artist_id': row.artist_id} for row in result]
	else:
		result = [{'scrobbles': row.count, 'real_scrobbles': row.really_by_this_artist, 'artist_id': row.artist_id} for row in result]
	result = rank(result, key='scrobbles')
	return result


@connection_provider
def count_scrobbles_by_track_impl(since, to, resolve_ids=True, dbconn=None):
	"""
	Count scrobbles grouped by track.

	Args:
		since: Start timestamp
		to: End timestamp
		resolve_ids: Include track info in result
		dbconn: Database connection

	Returns:
		List of dicts with scrobbles, track_id (and track if resolve_ids=True)
	"""
	op = sql.select(
		sql.func.count(sql.func.distinct(DB['scrobbles'].c.timestamp)).label('count'),
		DB['scrobbles'].c.track_id
	).select_from(DB['scrobbles']).where(
		DB['scrobbles'].c.timestamp <= to,
		DB['scrobbles'].c.timestamp >= since
	).group_by(DB['scrobbles'].c.track_id).order_by(sql.desc('count'))
	result = dbconn.execute(op).all()

	if resolve_ids:
		tracks = get_tracks_map([row.track_id for row in result], dbconn=dbconn)
		result = [{'scrobbles': row.count, 'track': tracks[row.track_id], 'track_id': row.track_id} for row in result]
	else:
		result = [{'scrobbles': row.count, 'track_id': row.track_id} for row in result]
	result = rank(result, key='scrobbles')
	return result


@connection_provider
def count_scrobbles_by_album_impl(since, to, resolve_ids=True, dbconn=None):
	"""
	Count scrobbles grouped by album.

	Args:
		since: Start timestamp
		to: End timestamp
		resolve_ids: Include album info in result
		dbconn: Database connection

	Returns:
		List of dicts with scrobbles, album_id (and album if resolve_ids=True)
	"""
	jointable = sql.join(
		DB['scrobbles'],
		DB['tracks'],
		DB['scrobbles'].c.track_id == DB['tracks'].c.id
	)

	op = sql.select(
		sql.func.count(sql.func.distinct(DB['scrobbles'].c.timestamp)).label('count'),
		DB['tracks'].c.album_id
	).select_from(jointable).where(
		DB['scrobbles'].c.timestamp <= to,
		DB['scrobbles'].c.timestamp >= since,
		DB['tracks'].c.album_id != None
	).group_by(DB['tracks'].c.album_id).order_by(sql.desc('count'))
	result = dbconn.execute(op).all()

	if resolve_ids:
		albums = get_albums_map([row.album_id for row in result], dbconn=dbconn)
		result = [{'scrobbles': row.count, 'album': albums[row.album_id], 'album_id': row.album_id} for row in result]
	else:
		result = [{'scrobbles': row.count, 'album_id': row.album_id} for row in result]
	result = rank(result, key='scrobbles')
	return result


@connection_provider
def count_scrobbles_by_album_combined_impl(since, to, artist, associated=False, resolve_ids=True, dbconn=None):
	"""
	Count scrobbles by all albums the artist is related to.

	Gets ALL albums the artist is in any way related to (either as trackartist or albumartist)
	and ranks them by scrobbles in the given time period.

	Args:
		since: Start timestamp
		to: End timestamp
		artist: Artist name or dict
		associated: Include associated/merged artists
		resolve_ids: Include album info in result
		dbconn: Database connection

	Returns:
		List of dicts with scrobbles, album_id (and album if resolve_ids=True)
	"""
	from doreah.logging import log

	artist_id = get_artist_id(artist, dbconn=dbconn)

	# Lazy init TEMP tables for fast path
	# This ensures _et is available for album charts optimization
	ensure_request_cache(artist_id, associated=associated, dbconn=dbconn)

	# Fast path: use _et TEMP table if available (artist page context)
	# This avoids expensive trackartists/albumartists joins
	et_check = dbconn.execute(sql.text("""
		SELECT name FROM sqlite_temp_master WHERE type='table' AND name='_et'
	""")).fetchone()

	if et_check:
		# Query using pre-cached entity tracks (raw SQL to avoid SQLAlchemy ambiguity with TEMP table)
		query = sql.text("""
			SELECT COUNT(DISTINCT s.timestamp) AS count, t.album_id
			FROM scrobbles s
			JOIN _et et ON et.track_id = s.track_id
			JOIN tracks t ON t.id = s.track_id
			WHERE s.timestamp BETWEEN :since AND :to
			  AND t.album_id IS NOT NULL
			GROUP BY t.album_id
			ORDER BY count DESC
		""")

		result = dbconn.execute(query, {"since": since, "to": to}).all()

		if resolve_ids:
			albums = get_albums_map([row.album_id for row in result], dbconn=dbconn)
			result = [{'scrobbles': row.count, 'album': albums[row.album_id], 'album_id': row.album_id} for row in result]
		else:
			result = [{'scrobbles': row.count, 'album_id': row.album_id} for row in result]
		result = rank(result, key='scrobbles')
		return result

	# Fallback path: original implementation
	if associated:
		artist_ids = get_associated_artists(artist, resolve_ids=False, dbconn=dbconn) + [artist_id]
	else:
		artist_ids = [artist_id]

	# get all tracks that either have a relevant trackartist
	# or are on an album with a relevant albumartist
	op1 = sql.select(DB['tracks'].c.id).select_from(
		sql.join(
			sql.join(
				DB['tracks'],
				DB['trackartists'],
				DB['tracks'].c.id == DB['trackartists'].c.track_id
			),
			DB['albumartists'],
			DB['tracks'].c.album_id == DB['albumartists'].c.album_id,
			isouter=True
		)
	).where(
		DB['tracks'].c.album_id.is_not(None),  # tracks without albums don't matter
		sql.or_(
			DB['trackartists'].c.artist_id.in_(artist_ids),
			DB['albumartists'].c.artist_id.in_(artist_ids)
		)
	)
	relevant_tracks = dbconn.execute(op1).all()
	relevant_track_ids = set(row.id for row in relevant_tracks)

	op2 = sql.select(
		sql.func.count(sql.func.distinct(DB['scrobbles'].c.timestamp)).label('count'),
		DB['tracks'].c.album_id
	).select_from(
		sql.join(
			DB['scrobbles'],
			DB['tracks'],
			DB['scrobbles'].c.track_id == DB['tracks'].c.id
		)
	).where(
		DB['scrobbles'].c.timestamp.between(since, to),
		DB['scrobbles'].c.track_id.in_(relevant_track_ids)
	).group_by(DB['tracks'].c.album_id).order_by(sql.desc('count'))
	result = dbconn.execute(op2).all()

	if resolve_ids:
		albums = get_albums_map([row.album_id for row in result], dbconn=dbconn)
		result = [{'scrobbles': row.count, 'album': albums[row.album_id], 'album_id': row.album_id} for row in result]
	else:
		result = [{'scrobbles': row.count, 'album_id': row.album_id} for row in result]
	result = rank(result, key='scrobbles')

	return result


@connection_provider
def count_scrobbles_by_album_of_artist_impl(since, to, artist, associated=False, resolve_ids=True, dbconn=None):
	"""
	Count scrobbles by albums of that artist.

	This ranks the albums of that artist, not albums the artist appears on.
	Counts ALL scrobbles on the album, even for tracks the artist is not part of!

	Args:
		since: Start timestamp
		to: End timestamp
		artist: Artist name or dict
		associated: Include associated/merged artists
		resolve_ids: Include album info in result
		dbconn: Database connection

	Returns:
		List of dicts with scrobbles, album_id (and album if resolve_ids=True)
	"""
	if associated:
		artist_ids = get_associated_artists(artist, resolve_ids=False, dbconn=dbconn) + [get_artist_id(artist, dbconn=dbconn)]
	else:
		artist_ids = [get_artist_id(artist, dbconn=dbconn)]

	jointable = sql.join(
		DB['scrobbles'],
		DB['tracks'],
		DB['scrobbles'].c.track_id == DB['tracks'].c.id
	)
	jointable2 = sql.join(
		jointable,
		DB['albumartists'],
		DB['tracks'].c.album_id == DB['albumartists'].c.album_id
	)

	op = sql.select(
		sql.func.count(sql.func.distinct(DB['scrobbles'].c.timestamp)).label('count'),
		DB['tracks'].c.album_id
	).select_from(jointable2).where(
		DB['scrobbles'].c.timestamp <= to,
		DB['scrobbles'].c.timestamp >= since,
		DB['albumartists'].c.artist_id.in_(artist_ids)
	).group_by(DB['tracks'].c.album_id).order_by(sql.desc('count'))
	result = dbconn.execute(op).all()

	if resolve_ids:
		albums = get_albums_map([row.album_id for row in result], dbconn=dbconn)
		result = [{'scrobbles': row.count, 'album': albums[row.album_id], 'album_id': row.album_id} for row in result]
	else:
		result = [{'scrobbles': row.count, 'album_id': row.album_id} for row in result]
	result = rank(result, key='scrobbles')
	return result


@connection_provider
def count_scrobbles_of_artist_by_album_impl(since, to, artist, associated=False, resolve_ids=True, dbconn=None):
	"""
	Count artist's track scrobbles grouped by album.

	This ranks the tracks of that artist by the album they appear on,
	even when the album is not the artist's.

	Args:
		since: Start timestamp
		to: End timestamp
		artist: Artist name or dict
		associated: Include associated/merged artists
		resolve_ids: Include album info in result
		dbconn: Database connection

	Returns:
		List of dicts with scrobbles, album_id (and album if resolve_ids=True)
	"""
	if associated:
		artist_ids = get_associated_artists(artist, resolve_ids=False, dbconn=dbconn) + [get_artist_id(artist, dbconn=dbconn)]
	else:
		artist_ids = [get_artist_id(artist, dbconn=dbconn)]

	jointable = sql.join(
		DB['scrobbles'],
		DB['trackartists'],
		DB['scrobbles'].c.track_id == DB['trackartists'].c.track_id
	)
	jointable2 = sql.join(
		jointable,
		DB['tracks'],
		DB['scrobbles'].c.track_id == DB['tracks'].c.id
	)

	op = sql.select(
		sql.func.count(sql.func.distinct(DB['scrobbles'].c.timestamp)).label('count'),
		DB['tracks'].c.album_id
	).select_from(jointable2).where(
		DB['scrobbles'].c.timestamp <= to,
		DB['scrobbles'].c.timestamp >= since,
		DB['trackartists'].c.artist_id.in_(artist_ids)
	).group_by(DB['tracks'].c.album_id).order_by(sql.desc('count'))
	result = dbconn.execute(op).all()

	if resolve_ids:
		albums = get_albums_map([row.album_id for row in result], dbconn=dbconn)
		result = [{'scrobbles': row.count, 'album': albums[row.album_id], 'album_id': row.album_id} for row in result if row.album_id]
	else:
		result = [{'scrobbles': row.count, 'album_id': row.album_id} for row in result]
	result = rank(result, key='scrobbles')
	return result


@connection_provider
def count_scrobbles_by_track_of_artist_impl(since, to, artist, associated=False, resolve_ids=True, dbconn=None):
	"""
	Count scrobbles by tracks of that artist.

	Args:
		since: Start timestamp
		to: End timestamp
		artist: Artist name or dict
		associated: Include associated/merged artists
		resolve_ids: Include track info in result
		dbconn: Database connection

	Returns:
		List of dicts with scrobbles, track_id (and track if resolve_ids=True)
	"""
	if associated:
		artist_ids = get_associated_artists(artist, resolve_ids=False, dbconn=dbconn) + [get_artist_id(artist, dbconn=dbconn)]
	else:
		artist_ids = [get_artist_id(artist, dbconn=dbconn)]

	jointable = sql.join(
		DB['scrobbles'],
		DB['trackartists'],
		DB['scrobbles'].c.track_id == DB['trackartists'].c.track_id
	)

	op = sql.select(
		sql.func.count(sql.func.distinct(DB['scrobbles'].c.timestamp)).label('count'),
		DB['scrobbles'].c.track_id
	).select_from(jointable).filter(
		DB['scrobbles'].c.timestamp <= to,
		DB['scrobbles'].c.timestamp >= since,
		DB['trackartists'].c.artist_id.in_(artist_ids)
	).group_by(DB['scrobbles'].c.track_id).order_by(sql.desc('count'))
	result = dbconn.execute(op).all()

	if resolve_ids:
		tracks = get_tracks_map([row.track_id for row in result], dbconn=dbconn)
		result = [{'scrobbles': row.count, 'track': tracks[row.track_id], 'track_id': row.track_id} for row in result]
	else:
		result = [{'scrobbles': row.count, 'track_id': row.track_id} for row in result]
	result = rank(result, key='scrobbles')
	return result


@connection_provider
def count_scrobbles_by_track_of_album_impl(since, to, album, resolve_ids=True, dbconn=None):
	"""
	Count scrobbles by tracks of that album.

	Args:
		since: Start timestamp
		to: End timestamp
		album: Album dict
		resolve_ids: Include track info in result
		dbconn: Database connection

	Returns:
		List of dicts with scrobbles, track_id (and track if resolve_ids=True)
	"""
	album_id = get_album_id(album, dbconn=dbconn) if album else None

	jointable = sql.join(
		DB['scrobbles'],
		DB['tracks'],
		DB['scrobbles'].c.track_id == DB['tracks'].c.id
	)

	op = sql.select(
		sql.func.count(sql.func.distinct(DB['scrobbles'].c.timestamp)).label('count'),
		DB['scrobbles'].c.track_id
	).select_from(jointable).filter(
		DB['scrobbles'].c.timestamp <= to,
		DB['scrobbles'].c.timestamp >= since,
		DB['tracks'].c.album_id == album_id
	).group_by(DB['scrobbles'].c.track_id).order_by(sql.desc('count'))
	result = dbconn.execute(op).all()

	if resolve_ids:
		tracks = get_tracks_map([row.track_id for row in result], dbconn=dbconn)
		result = [{'scrobbles': row.count, 'track': tracks[row.track_id], 'track_id': row.track_id} for row in result]
	else:
		result = [{'scrobbles': row.count, 'track_id': row.track_id} for row in result]
	result = rank(result, key='scrobbles')
	return result
