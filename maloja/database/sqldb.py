"""
Maloja Database API - Backward Compatibility Wrapper

This module provides the historical database API that external code expects.
It re-exports all database functions from their new organized locations while
preserving the original module identity (maloja.database.sqldb) for caching.

- Core layer: schema, connection, types, IDs, caching
- CRUD layer: scrobbles, tracks, artists, albums, associations, converters
- Queries layer: scrobbles, entities, relationships, search
- Charts layer: counts, tops, ranks, medals
- Maintenance layer: merges, cleanup, albums

All cached functions use thin wrappers in this module to preserve cache identity.
The @cached_wrapper decorators remain here so cached_function.__module__ stays
'maloja.database.sqldb', ensuring cache keys remain stable across the refactoring.

IMPORT ORGANIZATION:
Imports are organized by layer hierarchy (utils → core → crud → queries → charts → maintenance).
All functions are explicitly imported and listed in __all__ for a clear public API.

"""

from typing import TypedDict, Optional, cast

import sqlalchemy as sql
import json
import math
from datetime import datetime

from doreah.logging import log
from doreah.regular import runhourly, runmonthly

from ..pkg_global.conf import data_dir
from . import exceptions as exc
from . import no_aux_mode
from .dbcache import cached_wrapper, cached_wrapper_individual, invalidate_caches, invalidate_entity_cache

# Utils layer
from .utils.helpers import normalize_name, now, rank

# Core layer - Schema, Connection, Types, IDs, Cache
from .core.schema import DBTABLES, DB, meta, create_tables
from .core.connection import engine, connection_provider, get_maloja_info, set_maloja_info, set_sqlite_pragma
from .core.types import ScrobbleDict, TrackDict, AlbumDict
from .core.ids import get_track_id, get_artist_id, get_album_id, add_track_to_album, reset_id_caches
from .core.cache import ensure_request_cache, init_artist_request_cache

# CRUD layer - Scrobbles, Tracks, Artists, Albums, Associations, Converters
from .crud.scrobbles import add_scrobble, add_scrobbles, delete_scrobble, edit_scrobble, SCROBBLE_LOCK
from .crud.tracks import edit_track, add_tracks_to_albums, remove_album
from .crud.artists import edit_artist
from .crud.albums import edit_album
from .crud.associations import add_artists_to_tracks, remove_artists_from_tracks, add_artists_to_albums, remove_artists_from_albums
from .crud.converters import (
	scrobbles_db_to_dict, scrobble_db_to_dict,
	tracks_db_to_dict, track_db_to_dict,
	artists_db_to_dict, artist_db_to_dict,
	albums_db_to_dict, album_db_to_dict,
	scrobble_dict_to_db, track_dict_to_db,
	artist_dict_to_db, album_dict_to_db
)

# Queries layer - Scrobbles, Entities, Relationships, Search
from .queries.scrobbles import get_scrobbles, get_scrobbles_of_artist, get_scrobbles_of_track, get_scrobbles_of_album, get_scrobbles_num, get_artists_of_track, get_tracks_of_artist
from .queries.entities import get_artists, get_tracks, get_albums
from .queries.relationships import (
	get_artists_of_tracks, get_artists_of_albums, get_albums_of_artists, get_albums_artists_appear_on,
	get_tracks_map, get_artists_map, get_albums_map,
	get_associated_artists, get_associated_artist_map, get_credited_artists,
	get_track, get_artist, get_album, get_scrobble
)
from .queries.search import search_artist, search_track, search_album

# Maintenance layer - Merges, Cleanup, Albums
from .maintenance.merges import merge_tracks, merge_artists, merge_albums
from .maintenance.cleanup import clean_db, renormalize_names, merge_duplicate_tracks, merge_duplicate_albums
from .maintenance.albums import guess_albums


# Public API - explicitly list all exported functions and classes
# Alphabetized within each layer for readability
__all__ = [
	# Chart functions (count aggregations) - alphabetized
	'count_scrobbles_by_album',
	'count_scrobbles_by_album_combined',
	'count_scrobbles_by_album_of_artist',
	'count_scrobbles_by_artist',
	'count_scrobbles_by_track',
	'count_scrobbles_by_track_of_album',
	'count_scrobbles_by_track_of_artist',
	'count_scrobbles_of_artist_by_album',
	# Chart functions (top entities) - alphabetized
	'get_top_albums_direct',
	'get_top_artists_direct',
	'get_top_entities_direct',
	'get_top_tracks_direct',
	# Chart functions (rankings) - alphabetized
	'get_album_rank',
	'get_album_ranks_batch',
	'get_artist_rank',
	'get_artist_ranks_batch',
	'get_track_rank',
	'get_track_ranks_batch',
	# Chart functions (medals and topweeks) - alphabetized
	'get_album_medals_and_topweeks',
	'get_artist_medals_and_topweeks',
	'get_medals_and_topweeks',
	'get_track_medals_and_topweeks',

	# Maintenance functions - alphabetized
	'clean_db',
	'guess_albums',
	'merge_albums',
	'merge_artists',
	'merge_duplicate_albums',
	'merge_duplicate_tracks',
	'merge_tracks',
	'renormalize_names',

	# Re-exported from other modules
	# Utils - alphabetized
	'normalize_name',
	'now',
	'rank',
	# Schema - alphabetized
	'create_tables',
	'DB',
	'DBTABLES',
	'meta',
	# Connection - alphabetized
	'connection_provider',
	'engine',
	'get_maloja_info',
	'set_maloja_info',
	'set_sqlite_pragma',
	# Types - alphabetized
	'AlbumDict',
	'ScrobbleDict',
	'TrackDict',
	# ID resolution - alphabetized
	'add_track_to_album',
	'get_album_id',
	'get_artist_id',
	'get_track_id',
	'reset_id_caches',
	# Cache - alphabetized
	'ensure_request_cache',
	'init_artist_request_cache',
	# CRUD - Scrobbles - alphabetized
	'add_scrobble',
	'add_scrobbles',
	'delete_scrobble',
	'edit_scrobble',
	'SCROBBLE_LOCK',
	# CRUD - Tracks - alphabetized
	'add_tracks_to_albums',
	'edit_track',
	'remove_album',
	# CRUD - Artists - alphabetized
	'edit_artist',
	# CRUD - Albums - alphabetized
	'edit_album',
	# CRUD - Associations - alphabetized
	'add_artists_to_albums',
	'add_artists_to_tracks',
	'remove_artists_from_albums',
	'remove_artists_from_tracks',
	# Converters - DB to Dict - alphabetized
	'album_db_to_dict',
	'albums_db_to_dict',
	'artist_db_to_dict',
	'artists_db_to_dict',
	'scrobble_db_to_dict',
	'scrobbles_db_to_dict',
	'track_db_to_dict',
	'tracks_db_to_dict',
	# Converters - Dict to DB - alphabetized
	'album_dict_to_db',
	'artist_dict_to_db',
	'scrobble_dict_to_db',
	'track_dict_to_db',
	# Queries - Scrobbles - alphabetized
	'get_artists_of_track',
	'get_scrobbles',
	'get_scrobbles_num',
	'get_scrobbles_of_album',
	'get_scrobbles_of_artist',
	'get_scrobbles_of_track',
	'get_tracks_of_artist',
	# Queries - Entities - alphabetized
	'get_albums',
	'get_artists',
	'get_tracks',
	# Queries - Relationships - alphabetized
	'get_album',
	'get_albums_artists_appear_on',
	'get_albums_map',
	'get_albums_of_artists',
	'get_artist',
	'get_artists_map',
	'get_artists_of_albums',
	'get_artists_of_tracks',
	'get_associated_artist_map',
	'get_associated_artists',
	'get_credited_artists',
	'get_scrobble',
	'get_track',
	'get_tracks_map',
	# Queries - Search - alphabetized
	'search_album',
	'search_artist',
	'search_track',
	# Cache invalidation - alphabetized
	'invalidate_caches',
	'invalidate_entity_cache',
]


##### Database Initialization

# Create tables and run migrations at import time
# This must happen before any code tries to use the database
create_tables(engine)


##### Chart Functions - Wrapper Functions Preserving Cache Identity

# These wrapper functions maintain cache identity while delegating to implementations.
# The @cached_wrapper decorator here ensures cached_function.__module__ remains
# 'maloja.database.sqldb' for stable cache keys (Decision 6).

### Count Aggregations

@cached_wrapper
def count_scrobbles_by_artist(since, to, associated=True, resolve_ids=True, dbconn=None):
	"""
	Count scrobbles grouped by artist.

	Wrapper function preserving cache identity (maloja.database.sqldb.count_scrobbles_by_artist).
	Implementation in charts.counts.count_scrobbles_by_artist_impl.
	"""
	from .charts.counts import count_scrobbles_by_artist_impl
	return count_scrobbles_by_artist_impl(since, to, associated, resolve_ids, dbconn=dbconn)

@cached_wrapper
def count_scrobbles_by_track(since, to, resolve_ids=True, dbconn=None):
	"""
	Count scrobbles grouped by track.

	Wrapper function preserving cache identity (maloja.database.sqldb.count_scrobbles_by_track).
	Implementation in charts.counts.count_scrobbles_by_track_impl.
	"""
	from .charts.counts import count_scrobbles_by_track_impl
	return count_scrobbles_by_track_impl(since, to, resolve_ids, dbconn=dbconn)

@cached_wrapper
def count_scrobbles_by_album(since, to, resolve_ids=True, dbconn=None):
	"""
	Count scrobbles grouped by album.

	Wrapper function preserving cache identity (maloja.database.sqldb.count_scrobbles_by_album).
	Implementation in charts.counts.count_scrobbles_by_album_impl.
	"""
	from .charts.counts import count_scrobbles_by_album_impl
	return count_scrobbles_by_album_impl(since, to, resolve_ids, dbconn=dbconn)

@cached_wrapper
def count_scrobbles_by_album_combined(since, to, artist, associated=False, resolve_ids=True, dbconn=None):
	"""
	Count scrobbles by album for all albums an artist appears on.

	Wrapper function preserving cache identity (maloja.database.sqldb.count_scrobbles_by_album_combined).
	Implementation in charts.counts.count_scrobbles_by_album_combined_impl.
	"""
	from .charts.counts import count_scrobbles_by_album_combined_impl
	return count_scrobbles_by_album_combined_impl(since, to, artist, associated, resolve_ids, dbconn=dbconn)

@cached_wrapper
def count_scrobbles_by_album_of_artist(since, to, artist, associated=False, resolve_ids=True, dbconn=None):
	"""
	Count scrobbles by album for albums credited to the artist.

	Wrapper function preserving cache identity (maloja.database.sqldb.count_scrobbles_by_album_of_artist).
	Implementation in charts.counts.count_scrobbles_by_album_of_artist_impl.
	"""
	from .charts.counts import count_scrobbles_by_album_of_artist_impl
	return count_scrobbles_by_album_of_artist_impl(since, to, artist, associated, resolve_ids, dbconn=dbconn)

@cached_wrapper
def count_scrobbles_of_artist_by_album(since, to, artist, associated=False, resolve_ids=True, dbconn=None):
	"""
	Count scrobbles of artist's tracks grouped by album.

	Wrapper function preserving cache identity (maloja.database.sqldb.count_scrobbles_of_artist_by_album).
	Implementation in charts.counts.count_scrobbles_of_artist_by_album_impl.
	"""
	from .charts.counts import count_scrobbles_of_artist_by_album_impl
	return count_scrobbles_of_artist_by_album_impl(since, to, artist, associated, resolve_ids, dbconn=dbconn)

@cached_wrapper
def count_scrobbles_by_track_of_artist(since, to, artist, associated=False, resolve_ids=True, dbconn=None):
	"""
	Count scrobbles by track for an artist.

	Wrapper function preserving cache identity (maloja.database.sqldb.count_scrobbles_by_track_of_artist).
	Implementation in charts.counts.count_scrobbles_by_track_of_artist_impl.
	"""
	from .charts.counts import count_scrobbles_by_track_of_artist_impl
	return count_scrobbles_by_track_of_artist_impl(since, to, artist, associated, resolve_ids, dbconn=dbconn)

@cached_wrapper
def count_scrobbles_by_track_of_album(since, to, album, resolve_ids=True, dbconn=None):
	"""
	Count scrobbles by track for an album.

	Wrapper function preserving cache identity (maloja.database.sqldb.count_scrobbles_by_track_of_album).
	Implementation in charts.counts.count_scrobbles_by_track_of_album_impl.
	"""
	from .charts.counts import count_scrobbles_by_track_of_album_impl
	return count_scrobbles_by_track_of_album_impl(since, to, album, resolve_ids, dbconn=dbconn)


### Top Entities

@cached_wrapper
def get_top_entities_direct(entity_type, since, to, associated=True, resolve_ids=True, dbconn=None):
	"""
	Query scrobbles directly for top entities in time range.

	Wrapper function preserving cache identity (maloja.database.sqldb.get_top_entities_direct).
	Implementation in charts.tops.get_top_entities_direct_impl.
	"""
	from .charts.tops import get_top_entities_direct_impl
	return get_top_entities_direct_impl(entity_type, since, to, associated, resolve_ids, dbconn=dbconn)

@cached_wrapper
def get_top_artists_direct(since, to, associated=True, resolve_ids=True, dbconn=None):
	"""
	Query scrobbles directly for top artists.

	Wrapper function preserving cache identity (maloja.database.sqldb.get_top_artists_direct).
	Implementation in charts.tops.get_top_artists_direct_impl.
	"""
	from .charts.tops import get_top_artists_direct_impl
	return get_top_artists_direct_impl(since, to, associated, resolve_ids, dbconn=dbconn)

@cached_wrapper
def get_top_tracks_direct(since, to, resolve_ids=True, dbconn=None):
	"""
	Query scrobbles directly for top tracks.

	Wrapper function preserving cache identity (maloja.database.sqldb.get_top_tracks_direct).
	Implementation in charts.tops.get_top_tracks_direct_impl.
	"""
	from .charts.tops import get_top_tracks_direct_impl
	return get_top_tracks_direct_impl(since, to, resolve_ids, dbconn=dbconn)

@cached_wrapper
def get_top_albums_direct(since, to, resolve_ids=True, dbconn=None):
	"""
	Query scrobbles directly for top albums.

	Wrapper function preserving cache identity (maloja.database.sqldb.get_top_albums_direct).
	Implementation in charts.tops.get_top_albums_direct_impl.
	"""
	from .charts.tops import get_top_albums_direct_impl
	return get_top_albums_direct_impl(since, to, resolve_ids, dbconn=dbconn)


### Single Entity Ranks

@cached_wrapper
def get_track_rank(track_id, since, to, dbconn=None):
	"""
	Get rank and scrobble count for a single track within a time range.

	Wrapper function preserving cache identity (maloja.database.sqldb.get_track_rank).
	Implementation in charts.ranks.get_track_rank_impl.
	"""
	from .charts.ranks import get_track_rank_impl
	return get_track_rank_impl(track_id, since, to, dbconn=dbconn)

@cached_wrapper
def get_artist_rank(artist_id, since, to, associated=True, dbconn=None):
	"""
	Get rank and scrobble count for a single artist within a time range.

	Wrapper function preserving cache identity (maloja.database.sqldb.get_artist_rank).
	Implementation in charts.ranks.get_artist_rank_impl.
	"""
	from .charts.ranks import get_artist_rank_impl
	return get_artist_rank_impl(artist_id, since, to, associated, dbconn=dbconn)

@cached_wrapper
def get_album_rank(album_id, since, to, dbconn=None):
	"""
	Get rank and scrobble count for a single album within a time range.

	Wrapper function preserving cache identity (maloja.database.sqldb.get_album_rank).
	Implementation in charts.ranks.get_album_rank_impl.
	"""
	from .charts.ranks import get_album_rank_impl
	return get_album_rank_impl(album_id, since, to, dbconn=dbconn)


### Batch Rank Queries

@cached_wrapper
def get_track_ranks_batch(track_id, periods, dbconn=None):
	"""
	Get ranks for a single track across multiple time periods in one query.

	Wrapper function preserving cache identity (maloja.database.sqldb.get_track_ranks_batch).
	Implementation in charts.ranks.get_track_ranks_batch_impl.
	"""
	from .charts.ranks import get_track_ranks_batch_impl
	return get_track_ranks_batch_impl(track_id, periods, dbconn=dbconn)

@cached_wrapper
def get_artist_ranks_batch(artist_id, periods, associated=True, dbconn=None):
	"""
	Get ranks for a single artist across multiple time periods in one query.

	Wrapper function preserving cache identity (maloja.database.sqldb.get_artist_ranks_batch).
	Implementation in charts.ranks.get_artist_ranks_batch_impl.
	"""
	from .charts.ranks import get_artist_ranks_batch_impl
	return get_artist_ranks_batch_impl(artist_id, periods, associated, dbconn=dbconn)

@cached_wrapper
def get_album_ranks_batch(album_id, periods, dbconn=None):
	"""
	Get ranks for a single album across multiple time periods in one query.

	Wrapper function preserving cache identity (maloja.database.sqldb.get_album_ranks_batch).
	Implementation in charts.ranks.get_album_ranks_batch_impl.
	"""
	from .charts.ranks import get_album_ranks_batch_impl
	return get_album_ranks_batch_impl(album_id, periods, dbconn=dbconn)


### Medals and Topweeks

@cached_wrapper
def get_medals_and_topweeks(entity_type, entity_id, associated=True, dbconn=None):
	"""
	Calculate medals and topweeks for any entity type using window function queries.

	Wrapper function preserving cache identity (maloja.database.sqldb.get_medals_and_topweeks).
	Implementation in charts.medals.get_medals_and_topweeks_impl.
	"""
	from .charts.medals import get_medals_and_topweeks_impl
	return get_medals_and_topweeks_impl(entity_type, entity_id, associated, dbconn=dbconn)

@cached_wrapper
def get_artist_medals_and_topweeks(artist_id, associated=True, dbconn=None):
	"""
	Calculate medals and topweeks for an artist.

	Wrapper function preserving cache identity (maloja.database.sqldb.get_artist_medals_and_topweeks).
	Implementation in charts.medals.get_artist_medals_and_topweeks_impl.
	"""
	from .charts.medals import get_artist_medals_and_topweeks_impl
	return get_artist_medals_and_topweeks_impl(artist_id, associated, dbconn=dbconn)

@cached_wrapper
def get_track_medals_and_topweeks(track_id, dbconn=None):
	"""
	Calculate medals and topweeks for a track.

	Wrapper function preserving cache identity (maloja.database.sqldb.get_track_medals_and_topweeks).
	Implementation in charts.medals.get_track_medals_and_topweeks_impl.
	"""
	from .charts.medals import get_track_medals_and_topweeks_impl
	return get_track_medals_and_topweeks_impl(track_id, dbconn=dbconn)

@cached_wrapper
def get_album_medals_and_topweeks(album_id, dbconn=None):
	"""
	Calculate medals and topweeks for an album.

	Wrapper function preserving cache identity (maloja.database.sqldb.get_album_medals_and_topweeks).
	Implementation in charts.medals.get_album_medals_and_topweeks_impl.
	"""
	from .charts.medals import get_album_medals_and_topweeks_impl
	return get_album_medals_and_topweeks_impl(album_id, dbconn=dbconn)
