"""
Homepage Cache Builder

Builds pre-computed homepage tiles from direct scrobbles queries and stores them
in the homepage_cache table. This replaces potentially thousands of N+1 queries with 15 cache lookups.

Cache keys follow pattern: "home:top_artists:today", "home:top_tracks:week",
"home:top_albums:month", "home:top_artists:year", "home:top_tracks:all", etc.
"""

import json
import time
from enum import Enum
from threading import RLock, Thread
from typing import Optional, Dict, Any, List, Tuple, TypedDict
import sqlalchemy as sql
from sqlalchemy.engine import Engine, Connection
from doreah.logging import log


# Constants for time calculations
SECONDS_PER_DAY = 86400

# Number of items per tile (top 14 artists/tracks/albums to match template)
TOP_N_LIMIT = 14

# Expected number of cache tiles (for validation)
# 5 timeranges (today, week, month, year, all) × 3 entity types (artists, tracks, albums) = 15
EXPECTED_CACHE_TILES = 15

# Thread safety lock for cache operations (reentrant)
_CACHE_LOCK = RLock()

# Background rebuild tracking
_rebuild_in_progress = False
_rebuild_thread = None


# Type definitions for cache tile data structures
class ArtistTile(TypedDict):
	"""Type definition for artist tile entries"""
	artist: str
	artist_id: int
	rank: int
	scrobbles: int
	real_scrobbles: int


class TrackTile(TypedDict):
	"""Type definition for track tile entries"""
	track: str
	track_id: int
	artists: List[str]
	rank: int
	scrobbles: int
	real_scrobbles: int


class AlbumTile(TypedDict):
	"""Type definition for album tile entries"""
	album: str
	album_id: int
	artists: List[str]
	rank: int
	scrobbles: int
	real_scrobbles: int


# Cache key constants
class CacheKey(str, Enum):
	"""Cache key identifiers for homepage tiles (calendar-based timeranges)"""
	# Artists tiles (5 timeranges)
	TOP_ARTISTS_TODAY = "home:top_artists:today"
	TOP_ARTISTS_WEEK = "home:top_artists:week"
	TOP_ARTISTS_MONTH = "home:top_artists:month"
	TOP_ARTISTS_YEAR = "home:top_artists:year"
	TOP_ARTISTS_ALL = "home:top_artists:all"

	# Tracks tiles (5 timeranges)
	TOP_TRACKS_TODAY = "home:top_tracks:today"
	TOP_TRACKS_WEEK = "home:top_tracks:week"
	TOP_TRACKS_MONTH = "home:top_tracks:month"
	TOP_TRACKS_YEAR = "home:top_tracks:year"
	TOP_TRACKS_ALL = "home:top_tracks:all"

	# Albums tiles (5 timeranges)
	TOP_ALBUMS_TODAY = "home:top_albums:today"
	TOP_ALBUMS_WEEK = "home:top_albums:week"
	TOP_ALBUMS_MONTH = "home:top_albums:month"
	TOP_ALBUMS_YEAR = "home:top_albums:year"
	TOP_ALBUMS_ALL = "home:top_albums:all"


def _calculate_calendar_ranges(current_time: int, conn: Connection) -> Tuple[int, int, int, int]:
	"""
	Calculate calendar boundary timestamps for today, this week, this month, this year

	Uses SQLite date functions to match Maloja's calendar logic exactly.

	Args:
		current_time: Current Unix timestamp
		conn: Database connection (for SQLite date calculations)

	Returns:
		Tuple of (today_start, week_start, month_start, year_start) as Unix timestamps
	"""
	# Today: midnight of current day
	today_start = (current_time // SECONDS_PER_DAY) * SECONDS_PER_DAY

	# This Week: Monday midnight of current week (using SQLite's weekday logic)
	result = conn.execute(sql.text("""
		SELECT CAST(strftime('%s', date(:ts, 'unixepoch', 'weekday 1', '-7 days')) AS INTEGER) AS week_start
	"""), {"ts": current_time})
	week_start = result.fetchone()[0]

	# This Month: 1st of current month at midnight
	result = conn.execute(sql.text("""
		SELECT CAST(strftime('%s', date(:ts, 'unixepoch', 'start of month')) AS INTEGER) AS month_start
	"""), {"ts": current_time})
	month_start = result.fetchone()[0]

	# This Year: January 1st of current year at midnight
	result = conn.execute(sql.text("""
		SELECT CAST(strftime('%s', date(:ts, 'unixepoch', 'start of year')) AS INTEGER) AS year_start
	"""), {"ts": current_time})
	year_start = result.fetchone()[0]

	return today_start, week_start, month_start, year_start


def build_homepage_cache(engine: Engine) -> None:
	"""
	Build all homepage cache tiles (thread-safe with error handling)

	This generates pre-computed tiles for 5 calendar-based timeranges:
	- Top 14 artists (today, this week, this month, this year, all time)
	- Top 14 tracks (today, this week, this month, this year, all time)
	- Top 14 albums (today, this week, this month, this year, all time)

	Total: 15 tiles (5 timeranges × 3 entity types)

	Args:
		engine: SQLAlchemy engine

	Raises:
		SQLAlchemyError: If database operations fail
		json.JSONEncodeError: If cache data serialization fails
	"""
	with _CACHE_LOCK:
		try:
			log("[Cache] Building homepage tiles...")
			start_time = time.time()

			current_time = int(time.time())

			tiles_built = 0

			with engine.begin() as conn:
				# Build top artists tiles
				tiles_built += _build_top_artists_tiles(conn, current_time)

				# Build top tracks tiles
				tiles_built += _build_top_tracks_tiles(conn, current_time)

				# Build top albums tiles
				tiles_built += _build_top_albums_tiles(conn, current_time)

			duration = time.time() - start_time
			log(f"[Cache] Built {tiles_built} homepage tiles in {duration:.2f}s")

		except sql.exc.SQLAlchemyError as e:
			log(f"[Cache] Database error building tiles: {e}")
			raise
		except json.JSONDecodeError as e:
			log(f"[Cache] JSON encoding error: {e}")
			raise
		except Exception as e:
			log(f"[Cache] Unexpected error building tiles: {e}")
			raise


def _build_top_artists_tiles(conn: Connection, current_time: int) -> int:
	"""Build top artists tiles for 5 calendar timeranges: today, week, month, year, all"""
	from .charts.tops import get_top_artists_direct_impl

	tiles_built = 0

	# Calculate calendar boundaries
	today_start, week_start, month_start, year_start = _calculate_calendar_ranges(current_time, conn)

	# Today
	top_artists = get_top_artists_direct_impl(since=today_start, to=current_time, associated=False, resolve_ids=True, limit=TOP_N_LIMIT, dbconn=conn)
	top_artists_today = _convert_charts_to_cache_format(top_artists, entity_type='artist')
	_store_cache_entry(conn, CacheKey.TOP_ARTISTS_TODAY.value, top_artists_today, current_time)
	tiles_built += 1

	# This Week
	top_artists = get_top_artists_direct_impl(since=week_start, to=current_time, associated=False, resolve_ids=True, limit=TOP_N_LIMIT, dbconn=conn)
	top_artists_week = _convert_charts_to_cache_format(top_artists, entity_type='artist')
	_store_cache_entry(conn, CacheKey.TOP_ARTISTS_WEEK.value, top_artists_week, current_time)
	tiles_built += 1

	# This Month
	top_artists = get_top_artists_direct_impl(since=month_start, to=current_time, associated=False, resolve_ids=True, limit=TOP_N_LIMIT, dbconn=conn)
	top_artists_month = _convert_charts_to_cache_format(top_artists, entity_type='artist')
	_store_cache_entry(conn, CacheKey.TOP_ARTISTS_MONTH.value, top_artists_month, current_time)
	tiles_built += 1

	# This Year
	top_artists = get_top_artists_direct_impl(since=year_start, to=current_time, associated=False, resolve_ids=True, limit=TOP_N_LIMIT, dbconn=conn)
	top_artists_year = _convert_charts_to_cache_format(top_artists, entity_type='artist')
	_store_cache_entry(conn, CacheKey.TOP_ARTISTS_YEAR.value, top_artists_year, current_time)
	tiles_built += 1

	# All Time (no time filter)
	top_artists = get_top_artists_direct_impl(since=0, to=None, associated=False, resolve_ids=True, limit=TOP_N_LIMIT, dbconn=conn)
	top_artists_all = _convert_charts_to_cache_format(top_artists, entity_type='artist')
	_store_cache_entry(conn, CacheKey.TOP_ARTISTS_ALL.value, top_artists_all, current_time)
	tiles_built += 1

	return tiles_built


def _convert_charts_to_cache_format(chart_results: List[Dict[str, Any]], entity_type: str) -> List[Dict[str, Any]]:
	"""
	Convert charts layer format to homepage cache format.

	Charts layer format:
	  - track: {"title": "...", "artists": ["A", "B"]}
	  - track_id: 123
	  - rank: 1
	  - scrobbles: 100

	Cache format:
	  - track: "..."
	  - track_id: 123
	  - rank: 1
	  - scrobbles: 100
	  - real_scrobbles: 100
	  - artists: ["A", "B"]

	Args:
		chart_results: Results from charts layer (get_top_*_direct_impl)
		entity_type: 'track', 'album', or 'artist'

	Returns:
		Results in cache format
	"""
	cache_results = []

	for item in chart_results:
		if entity_type == 'track':
			track_info = item['track']
			cache_entry = {
				'track': track_info['title'],
				'track_id': item['track_id'],
				'rank': item['rank'],
				'scrobbles': item['scrobbles'],
				'real_scrobbles': item['scrobbles'],
				'artists': track_info['artists']
			}
		elif entity_type == 'album':
			album_info = item['album']
			cache_entry = {
				'album': album_info['albumtitle'],
				'album_id': item['album_id'],
				'rank': item['rank'],
				'scrobbles': item['scrobbles'],
				'real_scrobbles': item['scrobbles'],
				'artists': album_info['artists']
			}
		elif entity_type == 'artist':
			# item['artist'] is already the artist name string
			cache_entry = {
				'artist': item['artist'],
				'artist_id': item['artist_id'],
				'rank': item['rank'],
				'scrobbles': item['scrobbles'],
				'real_scrobbles': item.get('real_scrobbles', item['scrobbles'])
			}
		else:
			raise ValueError(f"Invalid entity_type: {entity_type}")

		cache_results.append(cache_entry)

	return cache_results


def _build_top_tracks_tiles(conn: Connection, current_time: int) -> int:
	"""Build top tracks tiles for 5 calendar timeranges: today, week, month, year, all"""
	from .charts.tops import get_top_tracks_direct_impl

	tiles_built = 0

	# Calculate calendar boundaries
	today_start, week_start, month_start, year_start = _calculate_calendar_ranges(current_time, conn)

	# Today
	top_tracks = get_top_tracks_direct_impl(since=today_start, to=current_time, resolve_ids=True, limit=TOP_N_LIMIT, dbconn=conn)
	top_tracks_today = _convert_charts_to_cache_format(top_tracks, entity_type='track')
	_store_cache_entry(conn, CacheKey.TOP_TRACKS_TODAY.value, top_tracks_today, current_time)
	tiles_built += 1

	# This Week
	top_tracks = get_top_tracks_direct_impl(since=week_start, to=current_time, resolve_ids=True, limit=TOP_N_LIMIT, dbconn=conn)
	top_tracks_week = _convert_charts_to_cache_format(top_tracks, entity_type='track')
	_store_cache_entry(conn, CacheKey.TOP_TRACKS_WEEK.value, top_tracks_week, current_time)
	tiles_built += 1

	# This Month
	top_tracks = get_top_tracks_direct_impl(since=month_start, to=current_time, resolve_ids=True, limit=TOP_N_LIMIT, dbconn=conn)
	top_tracks_month = _convert_charts_to_cache_format(top_tracks, entity_type='track')
	_store_cache_entry(conn, CacheKey.TOP_TRACKS_MONTH.value, top_tracks_month, current_time)
	tiles_built += 1

	# This Year
	top_tracks = get_top_tracks_direct_impl(since=year_start, to=current_time, resolve_ids=True, limit=TOP_N_LIMIT, dbconn=conn)
	top_tracks_year = _convert_charts_to_cache_format(top_tracks, entity_type='track')
	_store_cache_entry(conn, CacheKey.TOP_TRACKS_YEAR.value, top_tracks_year, current_time)
	tiles_built += 1

	# All Time (no time filter)
	top_tracks = get_top_tracks_direct_impl(since=0, to=None, resolve_ids=True, limit=TOP_N_LIMIT, dbconn=conn)
	top_tracks_all = _convert_charts_to_cache_format(top_tracks, entity_type='track')
	_store_cache_entry(conn, CacheKey.TOP_TRACKS_ALL.value, top_tracks_all, current_time)
	tiles_built += 1

	return tiles_built


def _build_top_albums_tiles(conn: Connection, current_time: int) -> int:
	"""Build top albums tiles for 5 calendar timeranges: today, week, month, year, all"""
	from .charts.tops import get_top_albums_direct_impl

	tiles_built = 0

	# Calculate calendar boundaries
	today_start, week_start, month_start, year_start = _calculate_calendar_ranges(current_time, conn)

	# Today
	top_albums = get_top_albums_direct_impl(since=today_start, to=current_time, resolve_ids=True, limit=TOP_N_LIMIT, dbconn=conn)
	top_albums_today = _convert_charts_to_cache_format(top_albums, entity_type='album')
	_store_cache_entry(conn, CacheKey.TOP_ALBUMS_TODAY.value, top_albums_today, current_time)
	tiles_built += 1

	# This Week
	top_albums = get_top_albums_direct_impl(since=week_start, to=current_time, resolve_ids=True, limit=TOP_N_LIMIT, dbconn=conn)
	top_albums_week = _convert_charts_to_cache_format(top_albums, entity_type='album')
	_store_cache_entry(conn, CacheKey.TOP_ALBUMS_WEEK.value, top_albums_week, current_time)
	tiles_built += 1

	# This Month
	top_albums = get_top_albums_direct_impl(since=month_start, to=current_time, resolve_ids=True, limit=TOP_N_LIMIT, dbconn=conn)
	top_albums_month = _convert_charts_to_cache_format(top_albums, entity_type='album')
	_store_cache_entry(conn, CacheKey.TOP_ALBUMS_MONTH.value, top_albums_month, current_time)
	tiles_built += 1

	# This Year
	top_albums = get_top_albums_direct_impl(since=year_start, to=current_time, resolve_ids=True, limit=TOP_N_LIMIT, dbconn=conn)
	top_albums_year = _convert_charts_to_cache_format(top_albums, entity_type='album')
	_store_cache_entry(conn, CacheKey.TOP_ALBUMS_YEAR.value, top_albums_year, current_time)
	tiles_built += 1

	# All Time (no time filter)
	top_albums = get_top_albums_direct_impl(since=0, to=None, resolve_ids=True, limit=TOP_N_LIMIT, dbconn=conn)
	top_albums_all = _convert_charts_to_cache_format(top_albums, entity_type='album')
	_store_cache_entry(conn, CacheKey.TOP_ALBUMS_ALL.value, top_albums_all, current_time)
	tiles_built += 1

	return tiles_built


def _store_cache_entry(
	conn: Connection,
	key: str,
	data: List[Any],
	updated_at: int
) -> None:
	"""
	Store a cache entry in homepage_cache table

	Uses INSERT ... ON CONFLICT to update existing entries
	Cache never expires on time - only invalidated explicitly when scrobbles are inserted

	Args:
		conn: Database connection
		key: Cache key (e.g., "home:top_artists:week")
		data: Data to cache (List[ArtistTile] | List[TrackTile] | List[AlbumTile])
		updated_at: Unix timestamp when cached

	Raises:
		TypeError: If data contains non-serializable types
		ValueError: If data cannot be serialized to JSON
	"""
	try:
		json_value = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
	except (TypeError, ValueError) as e:
		log(f"[Cache] Failed to serialize cache data for key {key}: {e}")
		raise

	conn.execute(sql.text("""
		INSERT INTO homepage_cache (key, value, updated_at)
		VALUES (:key, :value, :updated)
		ON CONFLICT(key) DO UPDATE SET
			value = excluded.value,
			updated_at = excluded.updated_at
	"""), {
		"key": key,
		"value": json_value,
		"updated": updated_at
	})


def get_cached_tile(engine: Engine, key: str) -> Optional[List[Dict[str, Any]]]:
	"""
	Get a cached homepage tile

	Cache never expires on time - only invalidated explicitly when scrobbles are inserted

	Args:
		engine: SQLAlchemy engine
		key: Cache key (e.g., "home:top_artists:week")

	Returns:
		dict or None: Cached data if valid, None if corrupted or not found
	"""
	try:
		with engine.connect() as conn:
			result = conn.execute(sql.text("""
				SELECT value
				FROM homepage_cache
				WHERE key = :key
			"""), {"key": key})

			row = result.fetchone()
			if not row:
				return None

			value = row[0]

			try:
				return json.loads(value)
			except json.JSONDecodeError as e:
				log(f"[Cache] Corrupted cache entry for key {key}: {e}")
				# Delete corrupted entry in separate transaction
				with engine.begin() as delete_conn:
					delete_conn.execute(sql.text("DELETE FROM homepage_cache WHERE key = :key"), {"key": key})
				return None

	except sql.exc.SQLAlchemyError as e:
		log(f"[Cache] Database error reading cache for key {key}: {e}")
		return None


def _async_rebuild_worker(engine: Engine) -> None:
	"""
	Background worker function for cache rebuild (runs in separate thread)

	This function should only be called from schedule_async_rebuild().
	It rebuilds the cache and cleans expired entries without blocking page requests.

	Args:
		engine: SQLAlchemy engine
	"""
	global _rebuild_in_progress

	try:
		build_homepage_cache(engine)
		log("[Cache] Background rebuild completed successfully")
	except Exception as e:
		log(f"[Cache] Background rebuild failed: {e}")
	finally:
		_rebuild_in_progress = False


def schedule_async_rebuild(engine: Engine) -> bool:
	"""
	Schedule an asynchronous cache rebuild in background thread

	This function is NON-BLOCKING and returns immediately.
	If a rebuild is already in progress, this function does nothing.

	Args:
		engine: SQLAlchemy engine

	Returns:
		True if rebuild was scheduled, False if already in progress
	"""
	global _rebuild_in_progress, _rebuild_thread

	# Check if rebuild already in progress (fast path, no lock needed)
	if _rebuild_in_progress:
		return False

	# Acquire lock to prevent race condition
	with _CACHE_LOCK:
		# Double-check after acquiring lock
		if _rebuild_in_progress:
			return False

		# Mark rebuild as in progress
		_rebuild_in_progress = True

		# Start background thread
		_rebuild_thread = Thread(
			target=_async_rebuild_worker,
			args=(engine,),
			daemon=True,
			name="homepage_cache_rebuild"
		)
		_rebuild_thread.start()

		return True


def rebuild_cache_if_needed(engine: Engine) -> None:
	"""
	Rebuild cache if any tiles are missing (thread-safe)

	This is the main entry point for cache maintenance.
	Note: Cache never expires on time - this only checks if tiles exist.

	Uses double-checked locking to prevent concurrent rebuilds.

	Args:
		engine: SQLAlchemy engine
	"""
	# First check without lock (fast path)

	try:
		with engine.connect() as conn:
			# Check if homepage_cache table exists
			result = conn.execute(sql.text(
				"SELECT name FROM sqlite_master WHERE type='table' AND name='homepage_cache'"
			))
			if not result.fetchone():
				return

			# Check if all tiles exist (no expiry check - cache never expires)
			result = conn.execute(sql.text("""
				SELECT COUNT(*)
				FROM homepage_cache
			"""))

			valid_tiles = result.fetchone()[0]

		# Expected: 3 artist tiles + 3 track tiles + 3 album tiles + 1 week tile = 10
		if valid_tiles >= EXPECTED_CACHE_TILES:
			# Cache is fine, no rebuild needed
			return

		# Need to rebuild - acquire lock to prevent concurrent rebuilds
		# Note: build_homepage_cache() will acquire _CACHE_LOCK again (reentrant behavior handled by Python)
		log(f"[Cache] Found {valid_tiles}/{EXPECTED_CACHE_TILES} valid tiles - rebuilding cache")
		build_homepage_cache(engine)

	except sql.exc.SQLAlchemyError as e:
		log(f"[Cache] Error checking cache status: {e}")
		# Don't raise - cache rebuild failures shouldn't break calling code


def invalidate_homepage_cache(engine: Engine, conn: Optional[Connection] = None) -> None:
	"""
	Invalidate homepage cache and schedule async rebuild

	This deletes all cache entries and triggers a background rebuild.
	This function is NON-BLOCKING and returns immediately.

	Args:
		engine: SQLAlchemy engine
		conn: Optional connection to use (if already in a transaction)
	"""
	try:
		# If connection provided, use it (already in transaction)
		# Otherwise create new transaction
		if conn is not None:
			_invalidate_cache_in_connection(conn)
		else:
			with engine.begin() as new_conn:
				_invalidate_cache_in_connection(new_conn)

		# Schedule async rebuild after cache invalidation
		schedule_async_rebuild(engine)

	except sql.exc.SQLAlchemyError as e:
		# Non-critical: cache invalidation failure shouldn't break scrobble insertion
		log(f"[Warning] Failed to invalidate homepage cache: {e}")


def _invalidate_cache_in_connection(conn: Connection) -> None:
	"""Helper to invalidate cache using an existing connection"""
	# Check if homepage_cache table exists
	result = conn.execute(sql.text(
		"SELECT name FROM sqlite_master WHERE type='table' AND name='homepage_cache'"
	))
	if not result.fetchone():
		return

	# Delete all cache entries (will be rebuilt async)
	conn.execute(sql.text("""
		DELETE FROM homepage_cache
	"""))
