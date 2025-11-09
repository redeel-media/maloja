"""
Medals and Top Weeks Query Implementations (UNDECORATED)

This module contains the implementation functions for medals and topweeks queries.
The @cached_wrapper decorators remain in database/sqldb.py to preserve cache
identity (Decision 6 - Cache Function Identity Preservation).

Each function here is named with an _impl suffix and is called by a thin wrapper
in sqldb.py that has the @cached_wrapper decorator.

Medals are yearly awards (gold/silver/bronze) for top entities.
Topweeks are the count of weeks an entity was #1.

Functions:
- get_medals_and_topweeks_impl: Generic medals/topweeks calculation with window functions
- get_artist_medals_and_topweeks_impl: Artist medals convenience wrapper
- get_track_medals_and_topweeks_impl: Track medals convenience wrapper
- get_album_medals_and_topweeks_impl: Album medals convenience wrapper
"""

import sqlalchemy as sql
import time
import datetime

from ..core.connection import connection_provider
from ..core.cache import ensure_request_cache


@connection_provider
def get_medals_and_topweeks_impl(entity_type, entity_id, associated=True, dbconn=None):
	"""
	Calculate medals and topweeks for any entity type using window function queries.

	Uses dense_rank() window function to rank entities within each year/week period.
	Leverages generated column indexes (year, week_start) for fast time-based grouping.

	Args:
		entity_type: 'artist', 'track', or 'album'
		entity_id: Entity ID to calculate medals for
		associated: Whether to merge associated artists (only applies to artists)
		dbconn: Database connection

	Returns:
		dict with:
		- 'medals': {'gold': [years], 'silver': [years], 'bronze': [years]}
		- 'topweeks': count of #1 weeks

	Performance: 2 window function queries (medals + topweeks) instead of 600+ loop queries.
	Expected: <200ms total (medals: <100ms, topweeks: <100ms).
	"""
	from doreah.logging import log
	start_time = time.time()

	from ...malojatime import thisweek, thisyear

	# Build entity-specific queries using window functions
	# All entity types use window functions with dense_rank() for optimal performance
	if entity_type == 'artist':
		# Lazy init TEMP tables on first call
		# This ensures _tyc is available for artist queries
		ensure_request_cache(entity_id, associated=associated, dbconn=dbconn)

		# Key optimization: Only aggregate years/weeks where THIS artist actually has scrobbles
		# Avoids global materialization by filtering early via entity_tracks → my_years/my_weeks

		if associated:
			# Medals query with associated artist support - using _ayc from cache
			medals_query = sql.text("""
				WITH ranked AS (
					SELECT year, gid,
						dense_rank() OVER (PARTITION BY year ORDER BY c DESC) AS r
					FROM _ayc
				)
				SELECT year, r AS rank
				FROM ranked
				WHERE gid = :entity_id AND r <= 3
				ORDER BY year
			""")

			# Topweeks query with associated artist support
			topweeks_query = sql.text("""
				WITH associated_sources AS (
					SELECT :entity_id AS source_artist
					UNION
					SELECT source_artist
					FROM associated_artists
					WHERE target_artist = :entity_id
				),
				entity_tracks AS (
					SELECT DISTINCT ta.track_id
					FROM trackartists ta
					WHERE ta.artist_id IN (SELECT source_artist FROM associated_sources)
				),
				my_weeks AS (
					SELECT s.week_start
					FROM scrobbles s
					JOIN entity_tracks et ON et.track_id = s.track_id
					WHERE s.week_start IS NOT NULL AND s.week_start != :current_week
					GROUP BY s.week_start
				),
				track_week_counts AS (
					SELECT s.week_start, s.track_id, COUNT(*) AS c
					FROM scrobbles s
					WHERE s.week_start IN (SELECT week_start FROM my_weeks)
					GROUP BY s.week_start, s.track_id
				),
				artist_week_counts AS (
					SELECT
						twc.week_start,
						COALESCE(aa.target_artist, ta.artist_id) AS gid,
						SUM(twc.c) AS c
					FROM track_week_counts twc
					JOIN trackartists ta ON ta.track_id = twc.track_id
					LEFT JOIN associated_artists aa ON aa.source_artist = ta.artist_id
					GROUP BY twc.week_start, gid
				),
				ranks AS (
					SELECT week_start, gid,
						dense_rank() OVER (PARTITION BY week_start ORDER BY c DESC) AS r
					FROM artist_week_counts
				)
				SELECT COUNT(*) AS topweeks
				FROM ranks
				WHERE gid = :entity_id AND r = 1
			""")
		else:
			# Medals query WITHOUT associated artists - using _ayc from cache
			medals_query = sql.text("""
				WITH ranked AS (
					SELECT year, gid,
						dense_rank() OVER (PARTITION BY year ORDER BY c DESC) AS r
					FROM _ayc
				)
				SELECT year, r AS rank
				FROM ranked
				WHERE gid = :entity_id AND r <= 3
				ORDER BY year
			""")

			# Topweeks query WITHOUT associated artists
			topweeks_query = sql.text("""
				WITH entity_tracks AS (
					SELECT DISTINCT ta.track_id
					FROM trackartists ta
					WHERE ta.artist_id = :entity_id
				),
				my_weeks AS (
					SELECT s.week_start
					FROM scrobbles s
					JOIN entity_tracks et ON et.track_id = s.track_id
					WHERE s.week_start IS NOT NULL AND s.week_start != :current_week
					GROUP BY s.week_start
				),
				track_week_counts AS (
					SELECT s.week_start, s.track_id, COUNT(*) AS c
					FROM scrobbles s
					WHERE s.week_start IN (SELECT week_start FROM my_weeks)
					GROUP BY s.week_start, s.track_id
				),
				artist_week_counts AS (
					SELECT
						twc.week_start,
						ta.artist_id AS gid,
						SUM(twc.c) AS c
					FROM track_week_counts twc
					JOIN trackartists ta ON ta.track_id = twc.track_id
					GROUP BY twc.week_start, ta.artist_id
				),
				ranks AS (
					SELECT week_start, gid,
						dense_rank() OVER (PARTITION BY week_start ORDER BY c DESC) AS r
					FROM artist_week_counts
				)
				SELECT COUNT(*) AS topweeks
				FROM ranks
				WHERE gid = :entity_id AND r = 1
			""")

	elif entity_type == 'track':
		# Track: Simple 1:1 relationship, no pre-aggregation needed
		medals_query = sql.text("""
			WITH base AS (
				SELECT s.year, s.track_id AS gid
				FROM scrobbles s
			),
			my_years AS (
				SELECT year
				FROM base
				WHERE gid = :entity_id
				GROUP BY year
			),
			yearly AS (
				SELECT year, gid, COUNT(*) AS c
				FROM base
				WHERE year IN (SELECT year FROM my_years)
				GROUP BY year, gid
			),
			ranked AS (
				SELECT year, gid,
					dense_rank() OVER (PARTITION BY year ORDER BY c DESC) AS r
				FROM yearly
			)
			SELECT year, r AS rank
			FROM ranked
			WHERE gid = :entity_id AND r <= 3
			ORDER BY year
		""")

		topweeks_query = sql.text("""
			WITH base AS (
				SELECT s.week_start, s.track_id AS gid
				FROM scrobbles s
			),
			weekly AS (
				SELECT week_start, gid, COUNT(*) AS c
				FROM base
				GROUP BY week_start, gid
			),
			ranks AS (
				SELECT week_start, gid,
					dense_rank() OVER (PARTITION BY week_start ORDER BY c DESC) AS r
				FROM weekly
			)
			SELECT COUNT(*) AS topweeks
			FROM ranks
			WHERE gid = :entity_id AND r = 1
		""")

	elif entity_type == 'album':
		# Album: 1:1 track relationship, no pre-aggregation needed
		medals_query = sql.text("""
			WITH base AS (
				SELECT s.year, t.album_id AS gid
				FROM scrobbles s
				JOIN tracks t ON s.track_id = t.id
			),
			my_years AS (
				SELECT year
				FROM base
				WHERE gid = :entity_id
				GROUP BY year
			),
			yearly AS (
				SELECT year, gid, COUNT(*) AS c
				FROM base
				WHERE year IN (SELECT year FROM my_years)
				GROUP BY year, gid
			),
			ranked AS (
				SELECT year, gid,
					dense_rank() OVER (PARTITION BY year ORDER BY c DESC) AS r
				FROM yearly
			)
			SELECT year, r AS rank
			FROM ranked
			WHERE gid = :entity_id AND r <= 3
			ORDER BY year
		""")

		topweeks_query = sql.text("""
			WITH base AS (
				SELECT s.week_start, t.album_id AS gid
				FROM scrobbles s
				JOIN tracks t ON s.track_id = t.id
			),
			weekly AS (
				SELECT week_start, gid, COUNT(*) AS c
				FROM base
				GROUP BY week_start, gid
			),
			ranks AS (
				SELECT week_start, gid,
					dense_rank() OVER (PARTITION BY week_start ORDER BY c DESC) AS r
				FROM weekly
			)
			SELECT COUNT(*) AS topweeks
			FROM ranks
			WHERE gid = :entity_id AND r = 1
		""")

	else:
		raise ValueError(f"Invalid entity_type: {entity_type}")

	# Execute queries with timing
	medals_start = time.time()

	# Build query parameters - artists need current_year/current_week to filter incomplete periods
	if entity_type == 'artist':
		# Get current year number and current week_start timestamp
		current_year = thisyear()
		current_week = thisweek()

		# Convert to the actual values used in the database
		current_year_num = current_year.year if hasattr(current_year, 'year') else datetime.datetime.now().year
		current_week_stamp = current_week.first_stamp() if hasattr(current_week, 'first_stamp') else int(datetime.datetime.now().timestamp())

		query_params = {
			"entity_id": entity_id,
			"current_year": current_year_num,
			"current_week": current_week_stamp
		}
	else:
		query_params = {"entity_id": entity_id}

	medal_results = dbconn.execute(medals_query, query_params).fetchall()
	medals_time = time.time() - medals_start

	topweeks_start = time.time()
	topweeks_result = dbconn.execute(topweeks_query, query_params).fetchone()
	topweeks_time = time.time() - topweeks_start

	# Group medals by rank
	medals = {'gold': [], 'silver': [], 'bronze': []}
	for row in medal_results:
		year_str = str(row.year)
		if row.rank == 1:
			medals['gold'].append(year_str)
		elif row.rank == 2:
			medals['silver'].append(year_str)
		elif row.rank == 3:
			medals['bronze'].append(year_str)

	topweeks = topweeks_result[0] if topweeks_result else 0

	total_time = time.time() - start_time
	log(f"[MEDALS] {entity_type} TOTAL get_medals_and_topweeks: {total_time:.3f}s (medals: {medals_time:.3f}s, topweeks: {topweeks_time:.3f}s)")

	return {'medals': medals, 'topweeks': topweeks}


@connection_provider
def get_artist_medals_and_topweeks_impl(artist_id, associated=True, dbconn=None):
	"""
	Calculate medals and topweeks for an artist.

	Convenience wrapper around get_medals_and_topweeks_impl for artists.

	Args:
		artist_id: Artist ID to calculate medals for
		associated: Include associated/merged artists
		dbconn: Database connection

	Returns:
		dict with:
		- 'medals': {'gold': [years], 'silver': [years], 'bronze': [years]}
		- 'topweeks': count of #1 weeks
	"""
	return get_medals_and_topweeks_impl('artist', artist_id, associated=associated, dbconn=dbconn)


@connection_provider
def get_track_medals_and_topweeks_impl(track_id, dbconn=None):
	"""
	Calculate medals and topweeks for a track.

	Convenience wrapper around get_medals_and_topweeks_impl for tracks.

	Args:
		track_id: Track ID to calculate medals for
		dbconn: Database connection

	Returns:
		dict with:
		- 'medals': {'gold': [years], 'silver': [years], 'bronze': [years]}
		- 'topweeks': count of #1 weeks
	"""
	return get_medals_and_topweeks_impl('track', track_id, associated=False, dbconn=dbconn)


@connection_provider
def get_album_medals_and_topweeks_impl(album_id, dbconn=None):
	"""
	Calculate medals and topweeks for an album.

	Convenience wrapper around get_medals_and_topweeks_impl for albums.

	Args:
		album_id: Album ID to calculate medals for
		dbconn: Database connection

	Returns:
		dict with:
		- 'medals': {'gold': [years], 'silver': [years], 'bronze': [years]}
		- 'topweeks': count of #1 weeks
	"""
	return get_medals_and_topweeks_impl('album', album_id, associated=False, dbconn=dbconn)
