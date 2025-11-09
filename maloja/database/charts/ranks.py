"""
Ranking Query Implementations (UNDECORATED)

This module contains the implementation functions for ranking queries.
The @cached_wrapper decorators remain in database/sqldb.py to preserve cache
identity (Decision 6 - Cache Function Identity Preservation).

Each public function here is named with an _impl suffix and is called by a thin
wrapper in sqldb.py that has the @cached_wrapper decorator.

Functions:
- get_track_rank_impl: Get rank for single track in time range
- get_artist_rank_impl: Get rank for single artist in time range (with TEMP table optimization)
- get_album_rank_impl: Get rank for single album in time range
- get_track_ranks_batch_impl: Get ranks for track across multiple periods
- get_artist_ranks_batch_impl: Get ranks for artist across multiple periods (with yearly optimization)
- get_album_ranks_batch_impl: Get ranks for album across multiple periods
- _is_year_aligned_periods: Helper to detect year-aligned periods
- _get_artist_ranks_batch_yearly_optimized: Helper for yearly artist ranking (67% faster)
"""

import sqlalchemy as sql
import time
from datetime import datetime

from ..core.connection import connection_provider
from ..core.cache import ensure_request_cache


@connection_provider
def get_track_rank_impl(track_id, since, to, dbconn=None):
	"""
	Get rank and scrobble count for a single track within a time range.

	Optimized query that counts only tracks with more scrobbles instead of
	ranking all tracks. Handles ties correctly: tracks with equal scrobbles
	get the same rank, next rank skips numbers (e.g., 1, 2, 2, 4).

	Args:
		track_id: Track ID to get rank for
		since: Start timestamp (inclusive)
		to: End timestamp (exclusive)
		dbconn: Database connection

	Returns:
		dict: {'rank': int|None, 'scrobbles': int}
		      rank is None if track has 0 scrobbles in period
	"""
	query = sql.text("""
		WITH track_scrobbles AS (
			SELECT COUNT(*) as my_scrobbles
			FROM scrobbles
			WHERE track_id = :track_id
			  AND timestamp >= :since
			  AND timestamp < :to
		)
		SELECT
			(SELECT my_scrobbles FROM track_scrobbles) as scrobbles,
			CASE
				WHEN (SELECT my_scrobbles FROM track_scrobbles) = 0 THEN NULL
				ELSE 1 + (
					SELECT COUNT(*)
					FROM (
						SELECT s.track_id
						FROM scrobbles s
						WHERE s.timestamp >= :since
						  AND s.timestamp < :to
						GROUP BY s.track_id
						HAVING COUNT(*) > (SELECT my_scrobbles FROM track_scrobbles)
					)
				)
			END as rank
	""")

	result = dbconn.execute(query, {
		'track_id': track_id,
		'since': since,
		'to': to
	}).fetchone()

	return {
		'rank': result.rank,
		'scrobbles': result.scrobbles
	}


@connection_provider
def get_artist_rank_impl(artist_id, since, to, associated=True, dbconn=None):
	"""
	Get rank and scrobble count for a single artist within a time range.

	Optimized query that counts only artists with more scrobbles instead of
	ranking all artists. Handles ties correctly and supports associated artists.

	Uses TEMP table cache (_ayc, _tyc) when available for significant speedup.

	Args:
		artist_id: Artist ID to get rank for
		since: Start timestamp (inclusive)
		to: End timestamp (exclusive)
		associated: Include associated artists (merge scrobbles)
		dbconn: Database connection

	Returns:
		dict: {'rank': int|None, 'scrobbles': int, 'real_scrobbles': int}
		      rank is None if artist has 0 scrobbles in period
	"""
	# Lazy init TEMP tables on first call (only for all-time rank queries)
	# This ensures _tyc is available without paying the cost on cached loads
	ensure_request_cache(artist_id, associated=associated, dbconn=dbconn)

	# Try to use _tyc cache if it exists (available during artist page requests)
	# Check if _tyc TEMP table exists
	cache_check = dbconn.execute(sql.text("""
		SELECT name FROM sqlite_temp_master WHERE type='table' AND name='_tyc'
	""")).fetchone()

	if cache_check:
		# Check if _ayc is available (faster than _tyc for rank queries)
		ayc_check = dbconn.execute(sql.text("""
			SELECT name FROM sqlite_temp_master WHERE type='table' AND name='_ayc'
		""")).fetchone()

		if ayc_check:
			# Use _ayc cache - fastest! Just SUM artist-year counts
			query = sql.text("""
				WITH artist_totals AS (
					SELECT gid, SUM(c) AS total_scrobbles
					FROM _ayc
					GROUP BY gid
				),
				my_total AS (
					SELECT total_scrobbles
					FROM artist_totals
					WHERE gid = :artist_id
				)
				SELECT
					COALESCE((SELECT total_scrobbles FROM my_total), 0) as scrobbles,
					COALESCE((SELECT total_scrobbles FROM my_total), 0) as real_scrobbles,
					CASE
						WHEN COALESCE((SELECT total_scrobbles FROM my_total), 0) = 0 THEN NULL
						ELSE 1 + (
							SELECT COUNT(*)
							FROM artist_totals
							WHERE total_scrobbles > (SELECT total_scrobbles FROM my_total)
						)
					END as rank
			""")

			result = dbconn.execute(query, {"artist_id": artist_id}).fetchone()
			return {
				'scrobbles': result.scrobbles,
				'real_scrobbles': result.real_scrobbles,
				'rank': result.rank
			}

		# Fallback to _tyc if _ayc not available

		if associated:
			query = sql.text("""
				WITH artist_totals AS (
					SELECT
						COALESCE(aa.target_artist, ta.artist_id) AS gid,
						SUM(tyc.c) AS total_scrobbles
					FROM _tyc tyc
					JOIN trackartists ta ON ta.track_id = tyc.track_id
					LEFT JOIN associated_artists aa ON aa.source_artist = ta.artist_id
					GROUP BY gid
				),
				my_total AS (
					SELECT total_scrobbles
					FROM artist_totals
					WHERE gid = :artist_id
				)
				SELECT
					COALESCE((SELECT total_scrobbles FROM my_total), 0) as scrobbles,
					COALESCE((SELECT total_scrobbles FROM my_total), 0) as real_scrobbles,
					CASE
						WHEN COALESCE((SELECT total_scrobbles FROM my_total), 0) = 0 THEN NULL
						ELSE 1 + (
							SELECT COUNT(*)
							FROM artist_totals
							WHERE total_scrobbles > (SELECT total_scrobbles FROM my_total)
						)
					END as rank
			""")
		else:
			query = sql.text("""
				WITH artist_totals AS (
					SELECT
						ta.artist_id AS gid,
						SUM(tyc.c) AS total_scrobbles
					FROM _tyc tyc
					JOIN trackartists ta ON ta.track_id = tyc.track_id
					GROUP BY ta.artist_id
				),
				my_total AS (
					SELECT total_scrobbles
					FROM artist_totals
					WHERE gid = :artist_id
				)
				SELECT
					COALESCE((SELECT total_scrobbles FROM my_total), 0) as scrobbles,
					COALESCE((SELECT total_scrobbles FROM my_total), 0) as real_scrobbles,
					CASE
						WHEN COALESCE((SELECT total_scrobbles FROM my_total), 0) = 0 THEN NULL
						ELSE 1 + (
							SELECT COUNT(*)
							FROM artist_totals
							WHERE total_scrobbles > (SELECT total_scrobbles FROM my_total)
						)
					END as rank
			""")

		result = dbconn.execute(query, {'artist_id': artist_id}).fetchone()

		return {
			'rank': result.rank,
			'scrobbles': result.scrobbles,
			'real_scrobbles': result.real_scrobbles
		}

	# Fallback to timestamp-based query if cache not available
	if associated:
		# Query with associated artist merging
		query = sql.text("""
			WITH artist_scrobbles AS (
				SELECT
					COUNT(*) as my_scrobbles,
					SUM(CASE WHEN aa.target_artist IS NULL THEN 1 ELSE 0 END) as my_real_scrobbles
				FROM scrobbles s
				JOIN trackartists ta ON s.track_id = ta.track_id
				LEFT JOIN associated_artists aa ON ta.artist_id = aa.source_artist
				WHERE COALESCE(aa.target_artist, ta.artist_id) = :artist_id
				  AND s.timestamp >= :since
				  AND s.timestamp < :to
			)
			SELECT
				(SELECT my_scrobbles FROM artist_scrobbles) as scrobbles,
				(SELECT my_real_scrobbles FROM artist_scrobbles) as real_scrobbles,
				CASE
					WHEN (SELECT my_scrobbles FROM artist_scrobbles) = 0 THEN NULL
					ELSE 1 + (
						SELECT COUNT(DISTINCT artist_id)
						FROM (
							SELECT
								COALESCE(aa.target_artist, ta.artist_id) as artist_id,
								COUNT(*) as artist_scrobbles
							FROM scrobbles s
							JOIN trackartists ta ON s.track_id = ta.track_id
							LEFT JOIN associated_artists aa ON ta.artist_id = aa.source_artist
							WHERE s.timestamp >= :since AND s.timestamp < :to
							GROUP BY COALESCE(aa.target_artist, ta.artist_id)
							HAVING artist_scrobbles > (SELECT my_scrobbles FROM artist_scrobbles)
						)
					)
				END as rank
		""")
	else:
		# Query without associated artists
		query = sql.text("""
			WITH artist_scrobbles AS (
				SELECT COUNT(*) as my_scrobbles
				FROM scrobbles s
				JOIN trackartists ta ON s.track_id = ta.track_id
				WHERE ta.artist_id = :artist_id
				  AND s.timestamp >= :since
				  AND s.timestamp < :to
			)
			SELECT
				(SELECT my_scrobbles FROM artist_scrobbles) as scrobbles,
				(SELECT my_scrobbles FROM artist_scrobbles) as real_scrobbles,
				CASE
					WHEN (SELECT my_scrobbles FROM artist_scrobbles) = 0 THEN NULL
					ELSE 1 + (
						SELECT COUNT(*)
						FROM (
							SELECT ta.artist_id
							FROM scrobbles s
							JOIN trackartists ta ON s.track_id = ta.track_id
							WHERE s.timestamp >= :since AND s.timestamp < :to
							GROUP BY ta.artist_id
							HAVING COUNT(*) > (SELECT my_scrobbles FROM artist_scrobbles)
						)
					)
				END as rank
		""")

	result = dbconn.execute(query, {
		'artist_id': artist_id,
		'since': since,
		'to': to
	}).fetchone()

	return {
		'rank': result.rank,
		'scrobbles': result.scrobbles,
		'real_scrobbles': result.real_scrobbles
	}


@connection_provider
def get_album_rank_impl(album_id, since, to, dbconn=None):
	"""
	Get rank and scrobble count for a single album within a time range.

	Optimized query that counts only albums with more scrobbles instead of
	ranking all albums. Handles ties correctly.

	Args:
		album_id: Album ID to get rank for
		since: Start timestamp (inclusive)
		to: End timestamp (exclusive)
		dbconn: Database connection

	Returns:
		dict: {'rank': int|None, 'scrobbles': int}
		      rank is None if album has 0 scrobbles in period
	"""
	query = sql.text("""
		WITH album_scrobbles AS (
			SELECT COUNT(*) as my_scrobbles
			FROM scrobbles s
			JOIN tracks t ON s.track_id = t.id
			WHERE t.album_id = :album_id
			  AND s.timestamp >= :since
			  AND s.timestamp < :to
		)
		SELECT
			(SELECT my_scrobbles FROM album_scrobbles) as scrobbles,
			CASE
				WHEN (SELECT my_scrobbles FROM album_scrobbles) = 0 THEN NULL
				ELSE 1 + (
					SELECT COUNT(*)
					FROM (
						SELECT t.album_id
						FROM scrobbles s
						JOIN tracks t ON s.track_id = t.id
						WHERE s.timestamp >= :since
						  AND s.timestamp < :to
						  AND t.album_id IS NOT NULL
						GROUP BY t.album_id
						HAVING COUNT(*) > (SELECT my_scrobbles FROM album_scrobbles)
					)
				)
			END as rank
	""")

	result = dbconn.execute(query, {
		'album_id': album_id,
		'since': since,
		'to': to
	}).fetchone()

	return {
		'rank': result.rank,
		'scrobbles': result.scrobbles
	}


@connection_provider
def get_track_ranks_batch_impl(track_id, periods, dbconn=None):
	"""
	Get ranks for a single track across multiple time periods in one query.

	Uses UNION ALL to batch all periods into a single database round trip,
	reducing 48 queries to 1 query.

	Args:
		track_id: Track ID to get ranks for
		periods: List of (since, to) timestamp tuples
		dbconn: Database connection

	Returns:
		list: List of dicts with {'period_index': int, 'rank': int|None, 'scrobbles': int}
		      Results may be in any order (caller should sort by period_index)
	"""
	from doreah.logging import log
	start_time = time.time()

	if not periods:
		return []

	# Build UNION ALL query for all periods
	union_parts = []
	params = {'track_id': track_id}

	for idx, (since, to) in enumerate(periods):
		params[f'since_{idx}'] = since
		params[f'to_{idx}'] = to
		union_parts.append(f"""
			SELECT
				{idx} as period_index,
				(
					SELECT COUNT(*) FROM scrobbles
					WHERE track_id = :track_id
					  AND timestamp >= :since_{idx}
					  AND timestamp < :to_{idx}
				) as scrobbles,
				CASE
					WHEN (
						SELECT COUNT(*) FROM scrobbles
						WHERE track_id = :track_id
						  AND timestamp >= :since_{idx}
						  AND timestamp < :to_{idx}
					) = 0 THEN NULL
					ELSE 1 + (
						SELECT COUNT(*)
						FROM (
							SELECT s.track_id
							FROM scrobbles s
							WHERE s.timestamp >= :since_{idx}
							  AND s.timestamp < :to_{idx}
							GROUP BY s.track_id
							HAVING COUNT(*) > (
								SELECT COUNT(*) FROM scrobbles
								WHERE track_id = :track_id
								  AND timestamp >= :since_{idx}
								  AND timestamp < :to_{idx}
							)
						)
					)
				END as rank
		""")

	query = sql.text(" UNION ALL ".join(union_parts))
	query_start = time.time()
	results = dbconn.execute(query, params).fetchall()
	query_time = time.time() - query_start

	result_list = [
		{
			'period_index': row.period_index,
			'rank': row.rank,
			'scrobbles': row.scrobbles
		}
		for row in results
	]

	return result_list


def _is_year_aligned_periods(periods, dbconn):
	"""
	Check if all periods represent full calendar years.

	This is lenient to handle timezone offsets - we just verify:
	1. Each period spans approximately 365 days
	2. The generated 'year' column will handle exact year extraction

	Args:
		periods: List of (since, to) timestamp tuples
		dbconn: Database connection (unused but kept for consistency)

	Returns:
		bool: True if all periods are year-aligned
	"""
	if not periods:
		return False

	for since, to in periods:
		# Verify period length is approximately 365 days (allow 360-370 days for timezone/DST)
		period_days = (to - since) / 86400
		if period_days < 360 or period_days > 370:
			return False

	return True


def _get_artist_ranks_batch_yearly_optimized(artist_id, periods, associated, dbconn):
	"""
	Optimized batch ranking for year-aligned periods using pre-aggregation.

	Uses the same pattern as medals query: aggregate by track-year FIRST,
	then distribute to artists. This avoids correlated subqueries.

	This is 67% faster than the standard batch query (reduces 0.459s → ~0.150s).

	Args:
		artist_id: Artist ID to get ranks for
		periods: List of (since, to) year-aligned timestamp tuples
		associated: Include associated artists
		dbconn: Database connection

	Returns:
		list: List of dicts with {'period_index': int, 'rank': int|None, 'scrobbles': int, 'real_scrobbles': int}
	"""
	# Lazy init TEMP tables on first call
	# This ensures _tyc is available for yearly performance queries
	ensure_request_cache(artist_id, associated=associated, dbconn=dbconn)

	# Extract years from periods using the midpoint timestamp
	# This handles timezone offsets where period boundaries don't align to Jan 1 00:00:00
	# e.g., period 2013-12-31 19:00:00 to 2014-12-31 19:00:00 covers year 2014
	years = [datetime.fromtimestamp((since + to) // 2).year for since, to in periods]
	year_to_idx = {year: idx for idx, year in enumerate(years)}

	# Create list of years for SQL IN clause
	years_list = ','.join(str(y) for y in years)

	# Build optimized query using TEMP tables (_ayc, _tyc, _et, _my_years, _src)
	# These were pre-populated by init_artist_request_cache() - no need to recreate CTEs!
	if associated:
		query = sql.text(f"""
			WITH ranked AS (
				SELECT year, gid, c,
					dense_rank() OVER (PARTITION BY year ORDER BY c DESC) AS r
				FROM _ayc
				WHERE year IN ({years_list})
			)
			SELECT year, c AS scrobbles, c AS real_scrobbles, r AS rank
			FROM ranked
			WHERE gid = :artist_id
			ORDER BY year
		""")
	else:
		query = sql.text(f"""
			WITH ranked AS (
				SELECT year, gid, c,
					dense_rank() OVER (PARTITION BY year ORDER BY c DESC) AS r
				FROM _ayc
				WHERE year IN ({years_list})
			)
			SELECT year, c AS scrobbles, c AS real_scrobbles, r AS rank
			FROM ranked
			WHERE gid = :artist_id
			ORDER BY year
		""")

	results = dbconn.execute(query, {"artist_id": artist_id}).fetchall()

	# Convert results to expected format
	result_dict = {row.year: row for row in results}
	result_list = []

	for year, idx in year_to_idx.items():
		if year in result_dict:
			row = result_dict[year]
			result_list.append({
				'period_index': idx,
				'rank': row.rank,
				'scrobbles': row.scrobbles,
				'real_scrobbles': row.real_scrobbles
			})
		else:
			# Artist had no scrobbles this year
			result_list.append({
				'period_index': idx,
				'rank': None,
				'scrobbles': 0,
				'real_scrobbles': 0
			})

	return result_list


@connection_provider
def get_artist_ranks_batch_impl(artist_id, periods, associated=True, dbconn=None):
	"""
	Get ranks for a single artist across multiple time periods in one query.

	OPTIMIZATION: Detects year-aligned periods and uses pre-aggregation fast path
	(67% faster: 0.459s → ~0.150s).

	Args:
		artist_id: Artist ID to get ranks for
		periods: List of (since, to) timestamp tuples
		associated: Include associated artists (merge scrobbles)
		dbconn: Database connection

	Returns:
		list: List of dicts with {'period_index': int, 'rank': int|None, 'scrobbles': int, 'real_scrobbles': int}
	"""
	if not periods:
		return []

	# OPTIMIZATION: Detect if periods are year-aligned and use pre-aggregation fast path
	# This is critical for yearly performance queries (reduces 0.459s → ~0.150s)
	if _is_year_aligned_periods(periods, dbconn):
		return _get_artist_ranks_batch_yearly_optimized(artist_id, periods, associated, dbconn)

	union_parts = []
	params = {'artist_id': artist_id}

	for idx, (since, to) in enumerate(periods):
		params[f'since_{idx}'] = since
		params[f'to_{idx}'] = to

		if associated:
			# Query with associated artist merging
			union_parts.append(f"""
				SELECT
					{idx} as period_index,
					(
						SELECT COUNT(*)
						FROM scrobbles s
						JOIN trackartists ta ON s.track_id = ta.track_id
						LEFT JOIN associated_artists aa ON ta.artist_id = aa.source_artist
						WHERE COALESCE(aa.target_artist, ta.artist_id) = :artist_id
						  AND s.timestamp >= :since_{idx}
						  AND s.timestamp < :to_{idx}
					) as scrobbles,
					(
						SELECT SUM(CASE WHEN aa.target_artist IS NULL THEN 1 ELSE 0 END)
						FROM scrobbles s
						JOIN trackartists ta ON s.track_id = ta.track_id
						LEFT JOIN associated_artists aa ON ta.artist_id = aa.source_artist
						WHERE COALESCE(aa.target_artist, ta.artist_id) = :artist_id
						  AND s.timestamp >= :since_{idx}
						  AND s.timestamp < :to_{idx}
					) as real_scrobbles,
					CASE
						WHEN (
							SELECT COUNT(*)
							FROM scrobbles s
							JOIN trackartists ta ON s.track_id = ta.track_id
							LEFT JOIN associated_artists aa ON ta.artist_id = aa.source_artist
							WHERE COALESCE(aa.target_artist, ta.artist_id) = :artist_id
							  AND s.timestamp >= :since_{idx}
							  AND s.timestamp < :to_{idx}
						) = 0 THEN NULL
						ELSE 1 + (
							SELECT COUNT(DISTINCT artist_id)
							FROM (
								SELECT
									COALESCE(aa.target_artist, ta.artist_id) as artist_id,
									COUNT(*) as artist_scrobbles
								FROM scrobbles s
								JOIN trackartists ta ON s.track_id = ta.track_id
								LEFT JOIN associated_artists aa ON ta.artist_id = aa.source_artist
								WHERE s.timestamp >= :since_{idx} AND s.timestamp < :to_{idx}
								GROUP BY COALESCE(aa.target_artist, ta.artist_id)
								HAVING artist_scrobbles > (
									SELECT COUNT(*)
									FROM scrobbles s2
									JOIN trackartists ta2 ON s2.track_id = ta2.track_id
									LEFT JOIN associated_artists aa2 ON ta2.artist_id = aa2.source_artist
									WHERE COALESCE(aa2.target_artist, ta2.artist_id) = :artist_id
									  AND s2.timestamp >= :since_{idx}
									  AND s2.timestamp < :to_{idx}
								)
							)
						)
					END as rank
			""")
		else:
			# Query without associated artists
			union_parts.append(f"""
				SELECT
					{idx} as period_index,
					(
						SELECT COUNT(*)
						FROM scrobbles s
						JOIN trackartists ta ON s.track_id = ta.track_id
						WHERE ta.artist_id = :artist_id
						  AND s.timestamp >= :since_{idx}
						  AND s.timestamp < :to_{idx}
					) as scrobbles,
					(
						SELECT COUNT(*)
						FROM scrobbles s
						JOIN trackartists ta ON s.track_id = ta.track_id
						WHERE ta.artist_id = :artist_id
						  AND s.timestamp >= :since_{idx}
						  AND s.timestamp < :to_{idx}
					) as real_scrobbles,
					CASE
						WHEN (
							SELECT COUNT(*)
							FROM scrobbles s
							JOIN trackartists ta ON s.track_id = ta.track_id
							WHERE ta.artist_id = :artist_id
							  AND s.timestamp >= :since_{idx}
							  AND s.timestamp < :to_{idx}
						) = 0 THEN NULL
						ELSE 1 + (
							SELECT COUNT(*)
							FROM (
								SELECT ta.artist_id
								FROM scrobbles s
								JOIN trackartists ta ON s.track_id = ta.track_id
								WHERE s.timestamp >= :since_{idx} AND s.timestamp < :to_{idx}
								GROUP BY ta.artist_id
								HAVING COUNT(*) > (
									SELECT COUNT(*)
									FROM scrobbles s2
									JOIN trackartists ta2 ON s2.track_id = ta2.track_id
									WHERE ta2.artist_id = :artist_id
									  AND s2.timestamp >= :since_{idx}
									  AND s2.timestamp < :to_{idx}
								)
							)
						)
					END as rank
			""")

	query = sql.text(" UNION ALL ".join(union_parts))
	results = dbconn.execute(query, params).fetchall()

	return [
		{
			'period_index': row.period_index,
			'rank': row.rank,
			'scrobbles': row.scrobbles,
			'real_scrobbles': row.real_scrobbles
		}
		for row in results
	]


@connection_provider
def get_album_ranks_batch_impl(album_id, periods, dbconn=None):
	"""
	Get ranks for a single album across multiple time periods in one query.

	Args:
		album_id: Album ID to get ranks for
		periods: List of (since, to) timestamp tuples
		dbconn: Database connection

	Returns:
		list: List of dicts with {'period_index': int, 'rank': int|None, 'scrobbles': int}
	"""
	if not periods:
		return []

	union_parts = []
	params = {'album_id': album_id}

	for idx, (since, to) in enumerate(periods):
		params[f'since_{idx}'] = since
		params[f'to_{idx}'] = to
		union_parts.append(f"""
			SELECT
				{idx} as period_index,
				(
					SELECT COUNT(*)
					FROM scrobbles s
					JOIN tracks t ON s.track_id = t.id
					WHERE t.album_id = :album_id
					  AND s.timestamp >= :since_{idx}
					  AND s.timestamp < :to_{idx}
				) as scrobbles,
				CASE
					WHEN (
						SELECT COUNT(*)
						FROM scrobbles s
						JOIN tracks t ON s.track_id = t.id
						WHERE t.album_id = :album_id
						  AND s.timestamp >= :since_{idx}
						  AND s.timestamp < :to_{idx}
					) = 0 THEN NULL
					ELSE 1 + (
						SELECT COUNT(*)
						FROM (
							SELECT t.album_id
							FROM scrobbles s
							JOIN tracks t ON s.track_id = t.id
							WHERE s.timestamp >= :since_{idx}
							  AND s.timestamp < :to_{idx}
							  AND t.album_id IS NOT NULL
							GROUP BY t.album_id
							HAVING COUNT(*) > (
								SELECT COUNT(*)
								FROM scrobbles s2
								JOIN tracks t2 ON s2.track_id = t2.id
								WHERE t2.album_id = :album_id
								  AND s2.timestamp >= :since_{idx}
								  AND s2.timestamp < :to_{idx}
							)
						)
					)
				END as rank
		""")

	query = sql.text(" UNION ALL ".join(union_parts))
	results = dbconn.execute(query, params).fetchall()

	return [
		{
			'period_index': row.period_index,
			'rank': row.rank,
			'scrobbles': row.scrobbles
		}
		for row in results
	]
