from typing import TypedDict, Optional, cast

import sqlalchemy as sql
from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlalchemy.dialects.sqlite import insert as sqliteinsert
import json
import unicodedata
import math
from datetime import datetime
from threading import Lock

from ..pkg_global.conf import data_dir
from .dbcache import cached_wrapper, cached_wrapper_individual, invalidate_caches, invalidate_entity_cache
from . import exceptions as exc
from . import no_aux_mode

from doreah.logging import log
from doreah.regular import runhourly, runmonthly



##### DB Technical

# ============================================================================
# DBTABLES: Database Schema Reference
# ============================================================================
#
# PURPOSE:
#   This dictionary serves as the SQLAlchemy ORM schema reference.
#   It defines table structure, relationships, and constraints used by
#   SQLAlchemy's query builder and relationship mapper.
#
# IMPORTANT:
#   - Tables are NOT created from this dict (see migrations instead)
#   - Actual table creation: migrations/000_init.sql
#   - Performance indexes: migrations/001_database_optimization.sql
#   - This dict is ONLY used to generate SQLAlchemy Table() objects
#
# MIGRATION WORKFLOW:
#   1. Fresh Database:
#      - migrations/000_init.sql creates all tables
#      - migrations/001_database_optimization.sql creates indexes
#      - DBTABLES provides ORM schema reference
#
#   2. Existing Database:
#      - Tables already exist
#      - Unapplied migrations run on startup
#      - DBTABLES provides ORM schema reference
#
# MAKING SCHEMA CHANGES:
#   1. Update this DBTABLES dict (for ORM queries)
#   2. Create new migration NNN_description.sql
#   3. Migration handles actual database changes
#
# DO NOT:
#   - Add code that creates tables from this dict
#   - Duplicate migration SQL in Python code
#   - Use this as the source of truth (migrations are source of truth)
#
# ============================================================================

DBTABLES = {
	# name - type - foreign key - kwargs
	'_maloja':{
		'columns':[
			("key",                 sql.String,                                   {'primary_key':True}),
			("value",               sql.String,                                   {})
		],
		'extraargs':(),'extrakwargs':{},
		'indexes':[]
	},
	'scrobbles':{
		'columns':[
			("timestamp",           sql.Integer,                                  {'primary_key':True}),
			("rawscrobble",         sql.String,                                   {}),
			("origin",              sql.String,                                   {}),
			("duration",            sql.Integer,                                  {}),
			("track_id",            sql.Integer, sql.ForeignKey('tracks.id'),     {}),
			("extra",               sql.String,                                   {})
		],
		'extraargs':(),'extrakwargs':{},
		'indexes':[
			# Performance-critical indexes for timestamp-based queries
			('idx_scrobbles_ts', ['timestamp']),
			('idx_scrobbles_ts_track', ['timestamp', 'track_id']),
			('idx_scrobbles_track_ts', ['track_id', 'timestamp']),
			('idx_scrobbles_track_id', ['track_id']),
			('idx_scrobbles_duration', ['duration'])
		]
	},
	'tracks':{
		'columns':[
			("id",                  sql.Integer,                                  {'primary_key':True}),
			("title",               sql.String,                                   {}),
			("title_normalized",    sql.String,                                   {}),
			("length",              sql.Integer,                                  {}),
			("album_id",           sql.Integer, sql.ForeignKey('albums.id'),      {})
		],
		'extraargs':(),'extrakwargs':{'sqlite_autoincrement':True},
		'indexes':[
			# Track search and album relationship indexes
			('idx_tracks_title_normalized', ['title_normalized']),
			('idx_tracks_album_id', ['album_id']),
			('idx_tracks_album_title', ['album_id', 'title_normalized'])
		]
	},
	'artists':{
		'columns':[
			("id",                  sql.Integer,                                  {'primary_key':True}),
			("name",                sql.String,                                   {}),
			("name_normalized",     sql.String,                                   {})
		],
		'extraargs':(),'extrakwargs':{'sqlite_autoincrement':True},
		'indexes':[
			# Artist search and deduplication
			('idx_artists_name_normalized', ['name_normalized'])
		]
	},
	'albums':{
		'columns':[
			("id",                  sql.Integer,                                  {'primary_key':True}),
			("albtitle",            sql.String,                                   {}),
			("albtitle_normalized", sql.String,                                   {})
			#("albumartist",     sql.String,                                   {})
			# when an album has no artists, always use 'Various Artists'
		],
		'extraargs':(),'extrakwargs':{'sqlite_autoincrement':True},
		'indexes':[
			# Album search and deduplication
			('idx_albums_albtitle_normalized', ['albtitle_normalized'])
		]
	},
	'trackartists':{
		'columns':[
			("id",                  sql.Integer,                                  {'primary_key':True}),
			("artist_id",           sql.Integer, sql.ForeignKey('artists.id'),    {}),
			("track_id",            sql.Integer, sql.ForeignKey('tracks.id'),     {})
		],
		'extraargs':(sql.UniqueConstraint('artist_id', 'track_id'),),'extrakwargs':{},
		'indexes':[
			# Critical junction table indexes for N+1 prevention
			('idx_trackartists_track_id', ['track_id']),
			('idx_trackartists_artist_id', ['artist_id']),
			('idx_trackartists_artist_track', ['artist_id', 'track_id'])
		]
	},
	'albumartists':{
		'columns':[
			("id",                  sql.Integer,                                  {'primary_key':True}),
			("artist_id",           sql.Integer, sql.ForeignKey('artists.id'),    {}),
			("album_id",            sql.Integer, sql.ForeignKey('albums.id'),     {})
		],
		'extraargs':(sql.UniqueConstraint('artist_id', 'album_id'),),'extrakwargs':{},
		'indexes':[
			# Album-artist relationship indexes
			('idx_albumartists_album_id', ['album_id']),
			('idx_albumartists_artist_id', ['artist_id']),
			('idx_albumartists_artist_album', ['artist_id', 'album_id'])
		]
	},
#	'albumtracks':{
#		# tracks can be in multiple albums
#		'columns':[
#			("id",                  sql.Integer,                                  {'primary_key':True}),
#			("album_id",            sql.Integer, sql.ForeignKey('albums.id'),     {}),
#			("track_id",            sql.Integer, sql.ForeignKey('tracks.id'),     {})
#		],
#		'extraargs':(sql.UniqueConstraint('album_id', 'track_id'),),'extrakwargs':{}
#	},
	'associated_artists':{
		'columns':[
			("source_artist",       sql.Integer, sql.ForeignKey('artists.id'),    {}),
			("target_artist",       sql.Integer, sql.ForeignKey('artists.id'),    {})
		],
		'extraargs':(sql.UniqueConstraint('source_artist', 'target_artist'),),'extrakwargs':{},
		'indexes':[
			# Artist association/merge rule indexes
			('idx_associated_source', ['source_artist']),
			('idx_associated_target', ['target_artist']),
			('idx_associated_source_target', ['source_artist', 'target_artist'])
		]
	}
}




DB = {}

# Create SQLAlchemy engine with SQLite optimizations
# Use NullPool for single-user scenario (no connection pooling overhead)
# Set check_same_thread=False to allow connection sharing across threads
engine = sql.create_engine(
	f"sqlite:///{data_dir['scrobbles']('malojadb.sqlite')}",
	poolclass=NullPool,
	connect_args={'check_same_thread': False},
	echo=False
)

# Set persistent PRAGMA settings (these survive across connections)
# Must be set ONCE on database initialization
with engine.connect() as conn:
	# Enable Write-Ahead Logging for better concurrency
	# WAL mode allows readers and writers to operate simultaneously
	conn.execute(sql.text("PRAGMA journal_mode=WAL"))

	# Reduce fsync frequency for better performance
	# NORMAL = fsync after each checkpoint (good balance of safety/speed)
	conn.execute(sql.text("PRAGMA synchronous=NORMAL"))
	conn.commit()

# Configure SQLite PRAGMAs on every connection
# These are transient settings that must be set per connection
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
	"""
	Set SQLite PRAGMA settings on each connection.

	These optimize SQLite for single-user, read-heavy workloads:
	- foreign_keys: Enable referential integrity checks
	- temp_store: Store temp tables/indexes in memory (faster)
	- cache_size: 64MB cache (default is ~2MB)
	- wal_autocheckpoint: Auto-checkpoint WAL every 1000 pages
	"""
	cursor = dbapi_conn.cursor()
	cursor.execute("PRAGMA foreign_keys=ON")
	cursor.execute("PRAGMA temp_store=MEMORY")
	cursor.execute("PRAGMA cache_size=-64000")  # 64MB in kilobytes
	cursor.execute("PRAGMA wal_autocheckpoint=1000")
	cursor.close()

meta = sql.MetaData()

# Create SQLAlchemy table definitions from DBTABLES
# These are used by the ORM for queries and relationship mapping
# Actual table creation is handled by migration 000_init.sql
for tablename in DBTABLES:

	DB[tablename] = sql.Table(
		tablename, meta,
		*[sql.Column(colname,*args,**kwargs) for colname,*args,kwargs in DBTABLES[tablename]['columns']],
		*DBTABLES[tablename]['extraargs'],
		**DBTABLES[tablename]['extrakwargs']
	)

# Run migrations to ensure tables exist before any code tries to use them
# This is called at import time to handle the chicken-and-egg problem where
# conf.py tries to write to _maloja table during import
from . import migrations
migrations.run_migrations(engine)

# upgrade old database with new columns
with engine.begin() as conn:
	for tablename in DBTABLES:
		info = DBTABLES[tablename]
		table = DB[tablename]

		for colname,datatype,*args,kwargs in info['columns']:
			try:
				statement = f"ALTER TABLE {tablename} ADD {colname} {datatype().compile()}"
				conn.execute(sql.text(statement))
				log(f"Column {colname} was added to table {tablename}!")
				# TODO figure out how to compile foreign key references!
			except sql.exc.OperationalError as e:
				pass


# adding a scrobble could consist of multiple write operations that sqlite doesn't
# see as belonging together
SCROBBLE_LOCK = Lock()


# decorator that passes either the provided dbconn, or creates a separate one
# just for this function call
def connection_provider(func):

	def wrapper(*args,**kwargs):
		if kwargs.get("dbconn") is not None:
			return func(*args,**kwargs)
		else:
			with engine.connect() as connection:
				with connection.begin():
					kwargs['dbconn'] = connection
					return func(*args,**kwargs)

	wrapper.__innerfunc__ = func
	wrapper.__name__ = f"CONPR_{func.__name__}"
	return wrapper

@connection_provider
def get_maloja_info(keys,dbconn=None):
	op = DB['_maloja'].select().where(
		DB['_maloja'].c.key.in_(keys)
	)
	result = dbconn.execute(op).all()

	info = {}
	for row in result:
		info[row.key] = row.value
	return info

@connection_provider
def set_maloja_info(info,dbconn=None):
	for k in info:
		op = sqliteinsert(DB['_maloja']).values(
			key=k, value=info[k]
		).on_conflict_do_update(
			index_elements=['key'],
			set_={'value':info[k]}
		)
		dbconn.execute(op)

##### DB <-> Dict translations

## ATTENTION ALL ADVENTURERS
## this is what a scrobble dict will look like from now on
## this is the single canonical source of truth
## stop making different little dicts in every single function
## this is the schema that will definitely 100% stay like this and not
## randomly get changed two versions later
## here we go
#
# {
# 	"time":int,
# 	"track":{
# 		"artists":list,
# 		"title":string,
# 		"album":{
# 			"albumtitle":string,
# 			"artists":list
# 		},
# 		"length":None
# 	},
# 	"duration":int,
# 	"origin":string,
#	"extra":{string-keyed mapping for all flags with the scrobble},
#	"rawscrobble":{string-keyed mapping of the original scrobble received}
# }
#
# The last two fields are not returned under normal circumstances


class AlbumDict(TypedDict, total=False):
	albumtitle: str
	artists: list[str]
	album_id: int  # Optional: included when album is fetched from DB


class TrackDict(TypedDict, total=False):
	artists: list[str]
	title: str
	album: AlbumDict
	length: int | None
	track_id: int  # Optional: included when track is fetched from DB


class ScrobbleDict(TypedDict):
	time: int
	track: TrackDict
	duration: int
	origin: str
	extra: Optional[dict]
	rawscrobble: Optional[dict]


##### Conversions between DB and dicts

# These should work on whole lists and collect all the references,
# then look them up once and fill them in


### DB -> DICT
def scrobbles_db_to_dict(rows, include_internal=False, dbconn=None) -> list[ScrobbleDict]:
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
	return scrobbles_db_to_dict([row], dbconn=dbconn)[0]


def tracks_db_to_dict(rows, dbconn=None) -> list[TrackDict]:
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
	return tracks_db_to_dict([row], dbconn=dbconn)[0]


def artists_db_to_dict(rows, dbconn=None) -> list[str]:
	return [
		row.name
		for row in rows
	]


def artist_db_to_dict(row, dbconn=None) -> str:
	return artists_db_to_dict([row], dbconn=dbconn)[0]


def albums_db_to_dict(rows, dbconn=None) -> list[AlbumDict]:
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
	return albums_db_to_dict([row], dbconn=dbconn)[0]


### DICT -> DB
# These should return None when no data is in the dict so they can be used for update statements

def scrobble_dict_to_db(info: ScrobbleDict, update_album=False, dbconn=None):
	return {
		"timestamp": info.get('time'),
		"origin": info.get('origin'),
		"duration": info.get('duration'),
		"track_id": get_track_id(info.get('track'), update_album=update_album, dbconn=dbconn),
		"extra": json.dumps(info.get('extra')) if info.get('extra') else None,
		"rawscrobble": json.dumps(info.get('rawscrobble')) if info.get('rawscrobble') else None
	}


def track_dict_to_db(info: TrackDict, dbconn=None):
	return {
		"title": info.get('title'),
		"title_normalized": normalize_name(info.get('title', '')) or None,
		"length": info.get('length')
	}


def artist_dict_to_db(info: str, dbconn=None):
	return {
		"name": info,
		"name_normalized": normalize_name(info)
	}


def album_dict_to_db(info: AlbumDict, dbconn=None):
	return {
		"albtitle": info.get('albumtitle'),
		"albtitle_normalized": normalize_name(info.get('albumtitle'))
	}




##### Actual Database interactions

# TODO: remove all resolve_id args and do that logic outside the caching to improve hit chances
# TODO: maybe also factor out all intitial get entity funcs (some here, some in __init__) and throw exceptions

@connection_provider
def add_scrobble(scrobbledict: ScrobbleDict, update_album=False, dbconn=None):
	_, ex, er = add_scrobbles([scrobbledict], update_album=update_album, dbconn=dbconn)
	if er > 0:
		raise exc.DuplicateTimestamp(existing_scrobble=None, rejected_scrobble=scrobbledict)
		# TODO: actually pass existing scrobble
	elif ex > 0:
		raise exc.DuplicateScrobble(scrobble=scrobbledict)


@connection_provider
def add_scrobbles(scrobbleslist: list[ScrobbleDict], update_album=False, dbconn=None) -> tuple[int, int, int]:
	"""
	Add scrobbles to the database.

	Args:
		scrobbleslist: List of scrobble dictionaries to insert
		update_album: Whether to update album information
		dbconn: Database connection

	Returns:
		Tuple of (success_count, exists_count, errors_count)
	"""

	with SCROBBLE_LOCK:

	#	ops = [
	#		DB['scrobbles'].insert().values(
	#			**scrobble_dict_to_db(s,update_album=update_album,dbconn=dbconn)
	#		) for s in scrobbleslist
	#	]

		success, exists, errors = 0, 0, 0
		successful_timestamps = []

		for s in scrobbleslist:
			scrobble_entry = scrobble_dict_to_db(s, update_album=update_album, dbconn=dbconn)
			try:
				dbconn.execute(DB['scrobbles'].insert().values(
					**scrobble_entry
				))
				success += 1
				successful_timestamps.append(scrobble_entry['timestamp'])
			except sql.exc.IntegrityError:
				# get existing scrobble
				result = dbconn.execute(DB['scrobbles'].select().where(
					DB['scrobbles'].c.timestamp == scrobble_entry['timestamp']
				)).first()
				if result.track_id == scrobble_entry['track_id']:
					exists += 1
				else:
					errors += 1

	if errors > 0: log(f"{errors} Scrobbles have not been written to database (duplicate timestamps)!", color='red')
	if exists > 0: log(f"{exists} Scrobbles have not been written to database (already exist)", color='orange')
	return success, exists, errors


@connection_provider
def delete_scrobble(scrobble_id: int, dbconn=None) -> bool:

	with SCROBBLE_LOCK:

		op = DB['scrobbles'].delete().where(
			DB['scrobbles'].c.timestamp == scrobble_id
		)

		result = dbconn.execute(op)

	return True


@connection_provider
def add_track_to_album(track_id: int, album_id: int, replace=False, dbconn=None) -> bool:

	conditions = [
		DB['tracks'].c.id == track_id
	]
	if not replace:
		# if we dont want replacement, just update if there is no album yet
		conditions.append(
			DB['tracks'].c.album_id == None
		)

	op = DB['tracks'].update().where(
		*conditions
	).values(
		album_id=album_id
	)

	result = dbconn.execute(op)

	invalidate_entity_cache() # because album info has changed
	#invalidate_caches() # changing album info of tracks will change album charts
	# ARE YOU FOR REAL
	# it just took me like 3 hours to figure out that this one line makes the artist page load slow because
	# we call this func with every new scrobble that contains album info, even if we end up not changing the album
	# of course i was always debugging with the manual scrobble button which just doesnt send any album info
	# and because we expel all caches every single time, the artist page then needs to recalculate the weekly charts of
	# ALL OF RECORDED HISTORY in order to display top weeks
	# lmao
	# TODO: figure out something better
	return True


@connection_provider
def add_tracks_to_albums(track_to_album_id_dict: dict[int, int], replace=False, dbconn=None) -> bool:

	for track_id in track_to_album_id_dict:
		add_track_to_album(track_id,track_to_album_id_dict[track_id], replace=replace, dbconn=dbconn)
	return True


@connection_provider
def remove_album(*track_ids: list[int], dbconn=None) -> bool:

	DB['tracks'].update().where(
		DB['tracks'].c.track_id.in_(track_ids)
	).values(
		album_id=None
	)
	return True


### these will 'get' the ID of an entity, creating it if necessary

@cached_wrapper
@connection_provider
def get_track_id(trackdict: TrackDict, create_new=True, update_album=False, dbconn=None) -> int | None:
	ntitle = normalize_name(trackdict['title'])
	artist_ids = [get_artist_id(a, create_new=create_new, dbconn=dbconn) for a in trackdict['artists']]
	artist_ids = list(set(artist_ids))

	op = DB['tracks'].select().where(
		DB['tracks'].c.title_normalized == ntitle
	)
	result = dbconn.execute(op).all()
	for row in result:
		# check if the artists are the same
		foundtrackartists = []

		op = DB['trackartists'].select(
#			DB['trackartists'].c.artist_id
		).where(
			DB['trackartists'].c.track_id == row.id
		)
		result = dbconn.execute(op).all()
		match_artist_ids = [r.artist_id for r in result]
		#print("required artists",artist_ids,"this match",match_artist_ids)
		if set(artist_ids) == set(match_artist_ids):
			#print("ID for",trackdict['title'],"was",row[0])
			if trackdict.get('album') and create_new:
				# if we don't supply create_new, it means we just want to get info about a track
				# which means no need to write album info, even if it was new
				
				# if we havent set update_album, we only want to assign the album in case the track
				# has no album yet. this means we also only want to create a potentially new album in that case
				album_id = get_album_id(trackdict['album'],create_new=(update_album or not row.album_id),dbconn=dbconn)
				add_track_to_album(row.id,album_id,replace=update_album,dbconn=dbconn)

			return row.id

	if not create_new:
		return None

	#print("Creating new track")
	op = DB['tracks'].insert().values(
		**track_dict_to_db(trackdict, dbconn=dbconn)
	)
	result = dbconn.execute(op)
	track_id = result.inserted_primary_key[0]
	#print(track_id)

	for artist_id in artist_ids:
		op = DB['trackartists'].insert().values(
			track_id=track_id,
			artist_id=artist_id
		)
		result = dbconn.execute(op)
	#print("Created",trackdict['title'],track_id)

	if trackdict.get('album'):
		add_track_to_album(track_id, get_album_id(trackdict['album'], dbconn=dbconn), dbconn=dbconn)
	return track_id


@cached_wrapper
@connection_provider
def get_artist_id(artistname: str, create_new=True, dbconn=None) -> int | None:
	nname = normalize_name(artistname)
	#print("looking for",nname)

	op = DB['artists'].select().where(
		DB['artists'].c.name_normalized == nname
	)
	result = dbconn.execute(op).all()
	for row in result:
		#print("ID for",artistname,"was",row[0])
		return row.id

	if not create_new:
		return None

	op = DB['artists'].insert().values(
		name=artistname,
		name_normalized=nname
	)
	result = dbconn.execute(op)
	#print("Created",artistname,result.inserted_primary_key)
	return result.inserted_primary_key[0]


@cached_wrapper
@connection_provider
def get_album_id(albumdict: AlbumDict, create_new=True, ignore_albumartists=False, dbconn=None) -> int | None:
	ntitle = normalize_name(albumdict['albumtitle'])
	artist_ids = [get_artist_id(a, dbconn=dbconn) for a in (albumdict.get('artists') or [])]
	artist_ids = list(set(artist_ids))

	op = DB['albums'].select(
#		DB['albums'].c.id
	).where(
		DB['albums'].c.albtitle_normalized == ntitle
	)
	result = dbconn.execute(op).all()
	for row in result:
		if ignore_albumartists:
			return row.id
		else:
			# check if the artists are the same
			foundtrackartists = []

			op = DB['albumartists'].select(
	#			DB['albumartists'].c.artist_id
			).where(
				DB['albumartists'].c.album_id == row.id
			)
			result = dbconn.execute(op).all()
			match_artist_ids = [r.artist_id for r in result]
			#print("required artists",artist_ids,"this match",match_artist_ids)
			if set(artist_ids) == set(match_artist_ids):
				#print("ID for",albumdict['title'],"was",row[0])
				return row.id

	if not create_new:
		return None

	op = DB['albums'].insert().values(
		**album_dict_to_db(albumdict, dbconn=dbconn)
	)
	result = dbconn.execute(op)
	album_id = result.inserted_primary_key[0]

	for artist_id in artist_ids:
		op = DB['albumartists'].insert().values(
			album_id=album_id,
			artist_id=artist_id
		)
		result = dbconn.execute(op)
	#print("Created",trackdict['title'],track_id)
	return album_id


### Edit existing

@connection_provider
def edit_scrobble(scrobble_id: int, scrobbleupdatedict: dict, dbconn=None) -> bool:

	dbentry = scrobble_dict_to_db(scrobbleupdatedict,dbconn=dbconn)
	dbentry = {k: v for k, v in dbentry.items() if v}

	print("Updating scrobble", dbentry)

	with SCROBBLE_LOCK:

		op = DB['scrobbles'].update().where(
			DB['scrobbles'].c.timestamp == scrobble_id
		).values(
			**dbentry
		)

		dbconn.execute(op)
	return True


# edit function only for primary db information (not linked fields)
@connection_provider
def edit_artist(artist_id: int, artistupdatedict: str, dbconn=None) -> bool:

	artist = get_artist(artist_id)
	changedartist = artistupdatedict # well

	dbentry = artist_dict_to_db(artistupdatedict, dbconn=dbconn)
	dbentry = {k: v for k, v in dbentry.items() if v}

	existing_artist_id = get_artist_id(changedartist, create_new=False, dbconn=dbconn)
	if existing_artist_id not in (None, artist_id):
		raise exc.ArtistExists(changedartist)

	op = DB['artists'].update().where(
		DB['artists'].c.id == artist_id
	).values(
		**dbentry
	)
	result = dbconn.execute(op)
	return True


# edit function only for primary db information (not linked fields)
@connection_provider
def edit_track(track_id: int, trackupdatedict: dict, dbconn=None) -> bool:

	track = get_track(track_id, dbconn=dbconn)
	changedtrack: TrackDict = {**track, **trackupdatedict}

	dbentry = track_dict_to_db(trackupdatedict, dbconn=dbconn)
	dbentry = {k: v for k, v in dbentry.items() if v}

	existing_track_id = get_track_id(changedtrack, create_new=False, dbconn=dbconn)
	if existing_track_id not in (None, track_id):
		raise exc.TrackExists(changedtrack)

	op = DB['tracks'].update().where(
		DB['tracks'].c.id == track_id
	).values(
		**dbentry
	)
	result = dbconn.execute(op)
	return True


# edit function only for primary db information (not linked fields)
@connection_provider
def edit_album(album_id: int, albumupdatedict: dict, dbconn=None) -> bool:

	album = get_album(album_id, dbconn=dbconn)
	changedalbum: AlbumDict = {**album, **albumupdatedict}

	dbentry = album_dict_to_db(albumupdatedict, dbconn=dbconn)
	dbentry = {k: v for k, v in dbentry.items() if v}

	existing_album_id = get_album_id(changedalbum, create_new=False, dbconn=dbconn)
	if existing_album_id not in (None, album_id):
		raise exc.TrackExists(changedalbum)

	op = DB['albums'].update().where(
		DB['albums'].c.id == album_id
	).values(
		**dbentry
	)
	result = dbconn.execute(op)
	return True


### Edit associations

@connection_provider
def add_artists_to_tracks(track_ids: list[int], artist_ids: list[int], dbconn=None) -> bool:

	op = DB['trackartists'].insert().values([
		{'track_id': track_id, 'artist_id': artist_id}
		for track_id in track_ids for artist_id in artist_ids
	])

	result = dbconn.execute(op)
	# the resulting tracks could now be duplicates of existing ones
	# this also takes care of clean_db
	merge_duplicate_tracks(dbconn=dbconn)
	return True


@connection_provider
def remove_artists_from_tracks(track_ids: list[int], artist_ids: list[int], dbconn=None) -> bool:

	# only tracks that have at least one other artist
	subquery = DB['trackartists'].select().where(
		~DB['trackartists'].c.artist_id.in_(artist_ids)
	).with_only_columns(
		DB['trackartists'].c.track_id
	).distinct().alias('sub')

	op = DB['trackartists'].delete().where(
		sql.and_(
			DB['trackartists'].c.track_id.in_(track_ids),
			DB['trackartists'].c.artist_id.in_(artist_ids),
			DB['trackartists'].c.track_id.in_(subquery.select())
		)
	)

	result = dbconn.execute(op)
	# the resulting tracks could now be duplicates of existing ones
	# this also takes care of clean_db
	merge_duplicate_tracks(dbconn=dbconn)
	return True


@connection_provider
def add_artists_to_albums(album_ids: list[int], artist_ids: list[int], dbconn=None) -> bool:

	op = DB['albumartists'].insert().values([
		{'album_id':album_id,'artist_id':artist_id}
		for album_id in album_ids for artist_id in artist_ids
	])

	result = dbconn.execute(op)
	# the resulting albums could now be duplicates of existing ones
	# this also takes care of clean_db
	merge_duplicate_albums(dbconn=dbconn)
	return True


@connection_provider
def remove_artists_from_albums(album_ids: list[int], artist_ids: list[int], dbconn=None) -> bool:

	# no check here, albums are allowed to have zero artists

	op = DB['albumartists'].delete().where(
		sql.and_(
			DB['albumartists'].c.album_id.in_(album_ids),
			DB['albumartists'].c.artist_id.in_(artist_ids)
		)
	)

	result = dbconn.execute(op)
	# the resulting albums could now be duplicates of existing ones
	# this also takes care of clean_db
	merge_duplicate_albums(dbconn=dbconn)
	return True


### Merge

@connection_provider
def merge_tracks(target_id: int, source_ids: list[int], dbconn=None) -> bool:

	op = DB['scrobbles'].update().where(
		DB['scrobbles'].c.track_id.in_(source_ids)
	).values(
		track_id=target_id
	)
	result = dbconn.execute(op)
	clean_db(dbconn=dbconn)
	return True


@connection_provider
def merge_artists(target_id: int, source_ids: list[int], dbconn=None) -> bool:

	# some tracks could already have multiple of the to be merged artists

	# find literally all tracksartist entries that have any of the artists involved
	op = DB['trackartists'].select().where(
		DB['trackartists'].c.artist_id.in_(source_ids + [target_id])
	)
	result = dbconn.execute(op)

	track_ids = set(row.track_id for row in result)

	# now just delete them all lmao
	op = DB['trackartists'].delete().where(
		#DB['trackartists'].c.track_id.in_(track_ids),
		DB['trackartists'].c.artist_id.in_(source_ids + [target_id]),
	)

	result = dbconn.execute(op)

	# now add back the real new artist
	op = DB['trackartists'].insert().values([
		{'track_id':track_id,'artist_id':target_id}
		for track_id in track_ids
	])

	result = dbconn.execute(op)

	# same for albums
	op = DB['albumartists'].select().where(
		DB['albumartists'].c.artist_id.in_(source_ids + [target_id])
	)
	result = dbconn.execute(op)

	album_ids = set(row.album_id for row in result)

	op = DB['albumartists'].delete().where(
		DB['albumartists'].c.artist_id.in_(source_ids + [target_id]),
	)
	result = dbconn.execute(op)

	op = DB['albumartists'].insert().values([
		{'album_id':album_id,'artist_id':target_id}
		for album_id in album_ids
	])

	result = dbconn.execute(op)

#	tracks_artists = {}
#	for row in result:
#		tracks_artists.setdefault(row.track_id,[]).append(row.artist_id)
#
#	multiple = {k:v for k,v in tracks_artists.items() if len(v) > 1}
#
#	print([(get_track(k),[get_artist(a) for a in v]) for k,v in multiple.items()])
#
#	op = DB['trackartists'].update().where(
#		DB['trackartists'].c.artist_id.in_(source_ids)
#	).values(
#		artist_id=target_id
#	)
#	result = dbconn.execute(op)

	# this could have created duplicate tracks and albums
	merge_duplicate_tracks(artist_id=target_id, dbconn=dbconn)
	merge_duplicate_albums(artist_id=target_id, dbconn=dbconn)
	clean_db(dbconn=dbconn)
	return True


@connection_provider
def merge_albums(target_id: int, source_ids: list[int], dbconn=None) -> bool:

	op = DB['tracks'].update().where(
		DB['tracks'].c.album_id.in_(source_ids)
	).values(
		album_id=target_id
	)
	result = dbconn.execute(op)
	clean_db(dbconn=dbconn)
	return True


### Functions that get rows according to parameters

@cached_wrapper
@connection_provider
def get_scrobbles_of_artist(artist,since=None,to=None,resolve_references=True,limit=None,reverse=False,associated=False,dbconn=None):

	if since is None: since=0
	if to is None: to=now()

	if associated:
		artist_ids = get_associated_artists(artist,resolve_ids=False,dbconn=dbconn) + [get_artist_id(artist,create_new=False,dbconn=dbconn)]
	else:
		artist_ids = [get_artist_id(artist,create_new=False,dbconn=dbconn)]


	jointable = sql.join(DB['scrobbles'],DB['trackartists'],DB['scrobbles'].c.track_id == DB['trackartists'].c.track_id)

	op = jointable.select().where(
		DB['scrobbles'].c.timestamp.between(since,to),
		DB['trackartists'].c.artist_id.in_(artist_ids)
	)
	if reverse:
		op = op.order_by(sql.desc('timestamp'))
	else:
		op = op.order_by(sql.asc('timestamp'))
	if limit and not associated:
		# if we count associated we cant limit here because we remove stuff later!
		op = op.limit(limit)
	result = dbconn.execute(op).all()

	# remove duplicates (multiple associated artists in the song, e.g. Irene & Seulgi being both counted as Red Velvet)
	# distinct on doesn't seem to exist in sqlite
	if associated:
		seen = set()
		filtered_result = []
		for row in result:
			if row.timestamp not in seen:
				filtered_result.append(row)
				seen.add(row.timestamp)
		result = filtered_result
		if limit:
			result = result[:limit]



	if resolve_references:
		result = scrobbles_db_to_dict(result,dbconn=dbconn)
	#result = [scrobble_db_to_dict(row,resolve_references=resolve_references) for row in result]
	return result

@cached_wrapper
@connection_provider
def get_scrobbles_of_track(track,since=None,to=None,resolve_references=True,limit=None,reverse=False,dbconn=None):

	if since is None: since=0
	if to is None: to=now()

	track_id = get_track_id(track,create_new=False,dbconn=dbconn)


	op = DB['scrobbles'].select().where(
		DB['scrobbles'].c.timestamp<=to,
		DB['scrobbles'].c.timestamp>=since,
		DB['scrobbles'].c.track_id==track_id
	)
	if reverse:
		op = op.order_by(sql.desc('timestamp'))
	else:
		op = op.order_by(sql.asc('timestamp'))
	if limit:
		op = op.limit(limit)
	result = dbconn.execute(op).all()

	if resolve_references:
		result = scrobbles_db_to_dict(result)
	#result = [scrobble_db_to_dict(row) for row in result]
	return result

@cached_wrapper
@connection_provider
def get_scrobbles_of_album(album,since=None,to=None,resolve_references=True,limit=None,reverse=False,dbconn=None):

	if since is None: since=0
	if to is None: to=now()

	album_id = get_album_id(album,create_new=False,dbconn=dbconn)

	jointable = sql.join(DB['scrobbles'],DB['tracks'],DB['scrobbles'].c.track_id == DB['tracks'].c.id)

	op = jointable.select().where(
		DB['scrobbles'].c.timestamp<=to,
		DB['scrobbles'].c.timestamp>=since,
		DB['tracks'].c.album_id==album_id
	)
	if reverse:
		op = op.order_by(sql.desc('timestamp'))
	else:
		op = op.order_by(sql.asc('timestamp'))
	if limit:
		op = op.limit(limit)
	result = dbconn.execute(op).all()

	if resolve_references:
		result = scrobbles_db_to_dict(result)
	#result = [scrobble_db_to_dict(row) for row in result]
	return result

@cached_wrapper
@connection_provider
def get_scrobbles(since=None,to=None,resolve_references=True,limit=None,reverse=False,dbconn=None):


	if since is None: since=0
	if to is None: to=now()

	op = DB['scrobbles'].select().where(
		DB['scrobbles'].c.timestamp.between(since,to)
	)
	if reverse:
		op = op.order_by(sql.desc('timestamp'))
	else:
		op = op.order_by(sql.asc('timestamp'))
	if limit:
		op = op.limit(limit)


	result = dbconn.execute(op).all()

	if resolve_references:
		result = scrobbles_db_to_dict(result,dbconn=dbconn)
	#result = [scrobble_db_to_dict(row,resolve_references=resolve_references) for i,row in enumerate(result) if i<max]

	return result


# we can do that with above and resolve_references=False, but just testing speed
@cached_wrapper
@connection_provider
def get_scrobbles_num(since=None,to=None,dbconn=None):

	if since is None: since=0
	if to is None: to=now()

	op = sql.select(sql.func.count()).select_from(DB['scrobbles']).where(
		DB['scrobbles'].c.timestamp.between(since,to)
	)
	result = dbconn.execute(op).all()

	return result[0][0]

@cached_wrapper
@connection_provider
def get_artists_of_track(track_id,resolve_references=True,dbconn=None):

	op = DB['trackartists'].select().where(
		DB['trackartists'].c.track_id==track_id
	)
	result = dbconn.execute(op).all()

	artists = [get_artist(row.artist_id,dbconn=dbconn) if resolve_references else row.artist_id for row in result]
	return artists


@cached_wrapper
@connection_provider
def get_tracks_of_artist(artist,dbconn=None):

	artist_id = get_artist_id(artist,dbconn=dbconn)

	op = sql.join(DB['tracks'],DB['trackartists']).select().where(
		DB['trackartists'].c.artist_id==artist_id
	)
	result = dbconn.execute(op).all()

	return tracks_db_to_dict(result,dbconn=dbconn)

@cached_wrapper
@connection_provider
def get_artists(dbconn=None):

	op = DB['artists'].select()
	result = dbconn.execute(op).all()

	return artists_db_to_dict(result,dbconn=dbconn)

@cached_wrapper
@connection_provider
def get_tracks(dbconn=None):

	op = DB['tracks'].select()
	result = dbconn.execute(op).all()

	return tracks_db_to_dict(result,dbconn=dbconn)

@cached_wrapper
@connection_provider
def get_albums(dbconn=None):

	op = DB['albums'].select()
	result = dbconn.execute(op).all()

	return albums_db_to_dict(result,dbconn=dbconn)

### functions that count rows for parameters

@cached_wrapper
@connection_provider
def count_scrobbles_by_artist(since,to,associated=True,resolve_ids=True,dbconn=None):
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
		artistselect = sql.func.coalesce(DB['associated_artists'].c.target_artist,DB['trackartists'].c.artist_id)
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
		DB['scrobbles'].c.timestamp.between(since,to)
	).group_by(
		artistselect
	).order_by(sql.desc('count'),sql.desc('really_by_this_artist'))
	result = dbconn.execute(op).all()

	if resolve_ids:
		artists = get_artists_map([row.artist_id for row in result],dbconn=dbconn)
		result = [{'scrobbles':row.count,'real_scrobbles':row.really_by_this_artist,'artist':artists[row.artist_id],'artist_id':row.artist_id} for row in result]
	else:
		result = [{'scrobbles':row.count,'real_scrobbles':row.really_by_this_artist,'artist_id':row.artist_id} for row in result]
	result = rank(result,key='scrobbles')
	return result

@cached_wrapper
@connection_provider
def count_scrobbles_by_track(since,to,resolve_ids=True,dbconn=None):


	op = sql.select(
		sql.func.count(sql.func.distinct(DB['scrobbles'].c.timestamp)).label('count'),
		DB['scrobbles'].c.track_id
	).select_from(DB['scrobbles']).where(
		DB['scrobbles'].c.timestamp<=to,
		DB['scrobbles'].c.timestamp>=since
	).group_by(DB['scrobbles'].c.track_id).order_by(sql.desc('count'))
	result = dbconn.execute(op).all()

	if resolve_ids:
		tracks = get_tracks_map([row.track_id for row in result],dbconn=dbconn)
		result = [{'scrobbles':row.count,'track':tracks[row.track_id],'track_id':row.track_id} for row in result]
	else:
		result = [{'scrobbles':row.count,'track_id':row.track_id} for row in result]
	result = rank(result,key='scrobbles')
	return result

@cached_wrapper
@connection_provider
def count_scrobbles_by_album(since,to,resolve_ids=True,dbconn=None):

	jointable = sql.join(
		DB['scrobbles'],
		DB['tracks'],
		DB['scrobbles'].c.track_id == DB['tracks'].c.id
	)

	op = sql.select(
		sql.func.count(sql.func.distinct(DB['scrobbles'].c.timestamp)).label('count'),
		DB['tracks'].c.album_id
	).select_from(jointable).where(
		DB['scrobbles'].c.timestamp<=to,
		DB['scrobbles'].c.timestamp>=since,
		DB['tracks'].c.album_id != None
	).group_by(DB['tracks'].c.album_id).order_by(sql.desc('count'))
	result = dbconn.execute(op).all()

	if resolve_ids:
		albums = get_albums_map([row.album_id for row in result],dbconn=dbconn)
		result = [{'scrobbles':row.count,'album':albums[row.album_id],'album_id':row.album_id} for row in result]
	else:
		result = [{'scrobbles':row.count,'album_id':row.album_id} for row in result]
	result = rank(result,key='scrobbles')
	return result


### OPTIMIZED SCROBBLES QUERIES

@cached_wrapper
@connection_provider
def get_top_entities_direct(entity_type, since, to, associated=True, resolve_ids=True, dbconn=None):
	"""
	Query scrobbles directly for top entities in time range.

	Fast indexed query using appropriate indexes per entity type.
	Replaces get_top_artists_direct, get_top_tracks_direct, get_top_albums_direct.

	Args:
		entity_type: 'artist', 'track', or 'album'
		since: Start timestamp
		to: End timestamp
		associated: Include associated artists (only applies to artists, merged into main artist)
		resolve_ids: Include entity names/info in result
		dbconn: Database connection

	Returns:
		List of dicts with scrobbles, {entity}_id, rank (and entity info if resolve_ids=True)
		For artists also includes real_scrobbles (non-merged count)
	"""

	# Configuration for different entity types
	if entity_type == 'artist':
		id_col = 'artist_id'
		resolver = get_artists_map
		entity_key = 'artist'

		if associated:
			# Query with associated artist merging
			query = sql.text("""
				WITH artist_counts AS (
					SELECT
						COALESCE(aa.target_artist, ta.artist_id) as artist_id,
						COUNT(*) as scrobbles,
						SUM(CASE WHEN aa.target_artist IS NULL THEN 1 ELSE 0 END) as real_scrobbles
					FROM scrobbles s
					JOIN trackartists ta ON s.track_id = ta.track_id
					LEFT JOIN associated_artists aa ON ta.artist_id = aa.source_artist
					WHERE s.timestamp >= :since AND s.timestamp < :to
					GROUP BY COALESCE(aa.target_artist, ta.artist_id)
				)
				SELECT artist_id, scrobbles, real_scrobbles
				FROM artist_counts
				ORDER BY scrobbles DESC, real_scrobbles DESC
			""")
		else:
			# Query without associated artists
			query = sql.text("""
				SELECT
					ta.artist_id,
					COUNT(*) as scrobbles,
					COUNT(*) as real_scrobbles
				FROM scrobbles s
				JOIN trackartists ta ON s.track_id = ta.track_id
				WHERE s.timestamp >= :since AND s.timestamp < :to
				GROUP BY ta.artist_id
				ORDER BY scrobbles DESC
			""")

	elif entity_type == 'track':
		id_col = 'track_id'
		resolver = get_tracks_map
		entity_key = 'track'

		query = sql.text("""
			SELECT
				track_id,
				COUNT(*) as scrobbles
			FROM scrobbles
			WHERE timestamp >= :since AND timestamp < :to
			GROUP BY track_id
			ORDER BY scrobbles DESC
		""")

	elif entity_type == 'album':
		id_col = 'album_id'
		resolver = get_albums_map
		entity_key = 'album'

		query = sql.text("""
			SELECT
				t.album_id,
				COUNT(*) as scrobbles
			FROM scrobbles s
			JOIN tracks t ON s.track_id = t.id
			WHERE s.timestamp >= :since AND s.timestamp < :to
			  AND t.album_id IS NOT NULL
			GROUP BY t.album_id
			ORDER BY scrobbles DESC
		""")

	else:
		raise ValueError(f"Invalid entity_type: {entity_type}")

	# Execute query
	result = dbconn.execute(query, {"since": since, "to": to}).all()

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
	return result


# Convenience wrappers for backward compatibility
@cached_wrapper
@connection_provider
def get_top_artists_direct(since, to, associated=True, resolve_ids=True, dbconn=None):
	"""Query scrobbles directly for top artists. See get_top_entities_direct()."""
	return get_top_entities_direct('artist', since, to, associated=associated, resolve_ids=resolve_ids, dbconn=dbconn)


@cached_wrapper
@connection_provider
def get_top_tracks_direct(since, to, resolve_ids=True, dbconn=None):
	"""Query scrobbles directly for top tracks. See get_top_entities_direct()."""
	return get_top_entities_direct('track', since, to, associated=False, resolve_ids=resolve_ids, dbconn=dbconn)


@cached_wrapper
@connection_provider
def get_top_albums_direct(since, to, resolve_ids=True, dbconn=None):
	"""Query scrobbles directly for top albums. See get_top_entities_direct()."""
	return get_top_entities_direct('album', since, to, associated=False, resolve_ids=resolve_ids, dbconn=dbconn)


# get ALL albums the artist is in any way related to and rank them by TBD
@cached_wrapper
@connection_provider
def count_scrobbles_by_album_combined(since,to,artist,associated=False,resolve_ids=True,dbconn=None):

	if associated:
		artist_ids = get_associated_artists(artist,resolve_ids=False,dbconn=dbconn) + [get_artist_id(artist,dbconn=dbconn)]
	else:
		artist_ids = [get_artist_id(artist,dbconn=dbconn)]

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
		DB['tracks'].c.album_id.is_not(None), # tracks without albums don't matter
		sql.or_(
			DB['trackartists'].c.artist_id.in_(artist_ids),
			DB['albumartists'].c.artist_id.in_(artist_ids)
		)
	)
	relevant_tracks = dbconn.execute(op1).all()
	relevant_track_ids = set(row.id for row in relevant_tracks)
	#for row in relevant_tracks:
	#	print(get_track(row.id))

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
		DB['scrobbles'].c.timestamp.between(since,to),
		DB['scrobbles'].c.track_id.in_(relevant_track_ids)
	).group_by(DB['tracks'].c.album_id).order_by(sql.desc('count'))
	result = dbconn.execute(op2).all()

	if resolve_ids:
		albums = get_albums_map([row.album_id for row in result],dbconn=dbconn)
		result = [{'scrobbles':row.count,'album':albums[row.album_id],'album_id':row.album_id} for row in result]
	else:
		result = [{'scrobbles':row.count,'album_id':row.album_id} for row in result]
	result = rank(result,key='scrobbles')

	#from pprint import pprint
	#pprint(result)
	return result


@cached_wrapper
@connection_provider
# this ranks the albums of that artist, not albums the artist appears on - even scrobbles
# of tracks the artist is not part of!
def count_scrobbles_by_album_of_artist(since,to,artist,associated=False,resolve_ids=True,dbconn=None):

	if associated:
		artist_ids = get_associated_artists(artist,resolve_ids=False,dbconn=dbconn) + [get_artist_id(artist,dbconn=dbconn)]
	else:
		artist_ids = [get_artist_id(artist,dbconn=dbconn)]

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
		DB['scrobbles'].c.timestamp<=to,
		DB['scrobbles'].c.timestamp>=since,
		DB['albumartists'].c.artist_id.in_(artist_ids)
	).group_by(DB['tracks'].c.album_id).order_by(sql.desc('count'))
	result = dbconn.execute(op).all()

	if resolve_ids:
		albums = get_albums_map([row.album_id for row in result],dbconn=dbconn)
		result = [{'scrobbles':row.count,'album':albums[row.album_id],'album_id':row.album_id} for row in result]
	else:
		result = [{'scrobbles':row.count,'album_id':row.album_id} for row in result]
	result = rank(result,key='scrobbles')
	return result

@cached_wrapper
@connection_provider
# this ranks the tracks of that artist by the album they appear on - even when the album
# is not the artist's
def count_scrobbles_of_artist_by_album(since,to,artist,associated=False,resolve_ids=True,dbconn=None):

	if associated:
		artist_ids = get_associated_artists(artist,resolve_ids=False,dbconn=dbconn) + [get_artist_id(artist,dbconn=dbconn)]
	else:
		artist_ids = [get_artist_id(artist,dbconn=dbconn)]

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
		DB['scrobbles'].c.timestamp<=to,
		DB['scrobbles'].c.timestamp>=since,
		DB['trackartists'].c.artist_id.in_(artist_ids)
	).group_by(DB['tracks'].c.album_id).order_by(sql.desc('count'))
	result = dbconn.execute(op).all()

	if resolve_ids:
		albums = get_albums_map([row.album_id for row in result],dbconn=dbconn)
		result = [{'scrobbles':row.count,'album':albums[row.album_id],'album_id':row.album_id} for row in result if row.album_id]
	else:
		result = [{'scrobbles':row.count,'album_id':row.album_id} for row in result]
	result = rank(result,key='scrobbles')
	return result


@cached_wrapper
@connection_provider
def count_scrobbles_by_track_of_artist(since,to,artist,associated=False,resolve_ids=True,dbconn=None):

	if associated:
		artist_ids = get_associated_artists(artist,resolve_ids=False,dbconn=dbconn) + [get_artist_id(artist,dbconn=dbconn)]
	else:
		artist_ids = [get_artist_id(artist,dbconn=dbconn)]

	jointable = sql.join(
		DB['scrobbles'],
		DB['trackartists'],
		DB['scrobbles'].c.track_id == DB['trackartists'].c.track_id
	)

	op = sql.select(
		sql.func.count(sql.func.distinct(DB['scrobbles'].c.timestamp)).label('count'),
		DB['scrobbles'].c.track_id
	).select_from(jointable).filter(
		DB['scrobbles'].c.timestamp<=to,
		DB['scrobbles'].c.timestamp>=since,
		DB['trackartists'].c.artist_id.in_(artist_ids)
	).group_by(DB['scrobbles'].c.track_id).order_by(sql.desc('count'))
	result = dbconn.execute(op).all()


	if resolve_ids:
		tracks = get_tracks_map([row.track_id for row in result],dbconn=dbconn)
		result = [{'scrobbles':row.count,'track':tracks[row.track_id],'track_id':row.track_id} for row in result]
	else:
		result = [{'scrobbles':row.count,'track_id':row.track_id} for row in result]
	result = rank(result,key='scrobbles')
	return result


@cached_wrapper
@connection_provider
def count_scrobbles_by_track_of_album(since,to,album,resolve_ids=True,dbconn=None):

	album_id = get_album_id(album,dbconn=dbconn) if album else None

	jointable = sql.join(
		DB['scrobbles'],
		DB['tracks'],
		DB['scrobbles'].c.track_id == DB['tracks'].c.id
	)

	op = sql.select(
		sql.func.count(sql.func.distinct(DB['scrobbles'].c.timestamp)).label('count'),
		DB['scrobbles'].c.track_id
	).select_from(jointable).filter(
		DB['scrobbles'].c.timestamp<=to,
		DB['scrobbles'].c.timestamp>=since,
		DB['tracks'].c.album_id==album_id
	).group_by(DB['scrobbles'].c.track_id).order_by(sql.desc('count'))
	result = dbconn.execute(op).all()


	if resolve_ids:
		tracks = get_tracks_map([row.track_id for row in result],dbconn=dbconn)
		result = [{'scrobbles':row.count,'track':tracks[row.track_id],'track_id':row.track_id} for row in result]
	else:
		result = [{'scrobbles':row.count,'track_id':row.track_id} for row in result]
	result = rank(result,key='scrobbles')
	return result



### Optimized Medal and Topweeks Calculation using Window Functions
# Single generic function replaces expensive loop-based medal calculations (many queries -> 1 query)

@cached_wrapper
@connection_provider
def get_medals_and_topweeks(entity_type, entity_id, associated=True, dbconn=None):
	"""
	Calculate medals and topweeks for any entity type using direct scrobbles query.

	Uses window functions to efficiently calculate:
	- Medals: Years where entity ranked #1/#2/#3 (gold/silver/bronze)
	- Topweeks: Count of weeks where entity was #1

	Single efficient query replacing 36+ queries (for 12-year user) with 1 query.

	Args:
		entity_type: 'artist', 'track', or 'album'
		entity_id: Entity ID to calculate medals for
		associated: Whether to merge associated artists (only applies to artists)
		dbconn: Database connection

	Returns:
		dict with:
		- 'medals': {'gold': [years], 'silver': [years], 'bronze': [years]}
		- 'topweeks': count of #1 weeks

	Performance: 36 queries -> 1 query for 12-year user (2s -> <1s page load)
	"""

	# Configuration for different entity types
	if entity_type == 'artist':
		id_col = 'artist_id'
		if associated:
			# Merge associated artists globally when calculating rankings
			medals_from = """FROM scrobbles s
				JOIN trackartists ta ON s.track_id = ta.track_id
				LEFT JOIN associated_artists aa ON ta.artist_id = aa.source_artist"""
			medals_group_by = "COALESCE(aa.target_artist, ta.artist_id)"
			topweeks_from = """FROM scrobbles s
				JOIN trackartists ta ON s.track_id = ta.track_id"""
			topweeks_group_by = "ta.artist_id"
			# Get associated artist IDs for topweeks filtering
			entity_ids = get_associated_artists(entity_id, resolve_ids=False, dbconn=dbconn) + [entity_id]
		else:
			medals_from = """FROM scrobbles s
				JOIN trackartists ta ON s.track_id = ta.track_id"""
			medals_group_by = "ta.artist_id"
			topweeks_from = medals_from
			topweeks_group_by = "ta.artist_id"
			entity_ids = [entity_id]

	elif entity_type == 'track':
		id_col = 'track_id'
		medals_from = "FROM scrobbles s"
		medals_group_by = "s.track_id"
		topweeks_from = "FROM scrobbles s"
		topweeks_group_by = "s.track_id"
		entity_ids = [entity_id]

	elif entity_type == 'album':
		id_col = 'album_id'
		medals_from = """FROM scrobbles s
			JOIN tracks t ON s.track_id = t.id
			WHERE t.album_id IS NOT NULL"""
		medals_group_by = "t.album_id"
		topweeks_from = """FROM scrobbles s
			JOIN tracks t ON s.track_id = t.id"""
		topweeks_group_by = "t.album_id"
		entity_ids = [entity_id]

	else:
		raise ValueError(f"Invalid entity_type: {entity_type}")

	# Build medals query
	medals_query = sql.text(f"""
		WITH yearly_rankings AS (
			SELECT
				{medals_group_by} as {id_col},
				CAST(strftime('%Y', datetime(s.timestamp, 'unixepoch')) AS INTEGER) AS year,
				COUNT(*) as scrobbles,
				DENSE_RANK() OVER (
					PARTITION BY CAST(strftime('%Y', datetime(s.timestamp, 'unixepoch')) AS INTEGER)
					ORDER BY COUNT(*) DESC
				) as rank
			{medals_from}
			GROUP BY {medals_group_by}, year
		)
		SELECT year, rank
		FROM yearly_rankings
		WHERE {id_col} = :entity_id
		  AND rank <= 3
		ORDER BY year
	""")

	# Build topweeks query
	if entity_type == 'artist' and associated:
		# For associated artists, filter by multiple IDs
		topweeks_query = sql.text(f"""
			WITH weekly_scrobbles AS (
				SELECT
					{topweeks_group_by} as {id_col},
					CAST(strftime('%s', date(s.timestamp, 'unixepoch', 'weekday 1', '-7 days')) AS INTEGER) AS week_start,
					COUNT(*) as cnt
				{topweeks_from}
				GROUP BY {topweeks_group_by}, week_start
			),
			ranked AS (
				SELECT
					{id_col},
					week_start,
					DENSE_RANK() OVER (PARTITION BY week_start ORDER BY cnt DESC) as rank
				FROM weekly_scrobbles
			)
			SELECT COUNT(*) as topweeks
			FROM ranked
			WHERE {id_col} IN :entity_ids
			  AND rank = 1
		""")
		topweeks_params = {"entity_ids": tuple(entity_ids)}
	else:
		# For single entity or non-associated artists
		topweeks_where = f"{topweeks_group_by} = :entity_id" if entity_type == 'album' else ""
		topweeks_query = sql.text(f"""
			WITH weekly_scrobbles AS (
				SELECT
					{topweeks_group_by} as {id_col},
					CAST(strftime('%s', date(s.timestamp, 'unixepoch', 'weekday 1', '-7 days')) AS INTEGER) AS week_start,
					COUNT(*) as cnt
				{topweeks_from}
				{"WHERE " + topweeks_where if topweeks_where else ""}
				GROUP BY {topweeks_group_by}, week_start
			),
			ranked AS (
				SELECT
					{id_col},
					week_start,
					DENSE_RANK() OVER (PARTITION BY week_start ORDER BY cnt DESC) as rank
				FROM weekly_scrobbles
			)
			SELECT COUNT(*) as topweeks
			FROM ranked
			WHERE {id_col} = :entity_id
			  AND rank = 1
		""")
		topweeks_params = {"entity_id": entity_id}

	# Execute medals query
	medal_results = dbconn.execute(medals_query, {"entity_id": entity_id}).fetchall()

	# Group by rank
	medals = {'gold': [], 'silver': [], 'bronze': []}
	for row in medal_results:
		year_str = str(row.year)
		if row.rank == 1:
			medals['gold'].append(year_str)
		elif row.rank == 2:
			medals['silver'].append(year_str)
		elif row.rank == 3:
			medals['bronze'].append(year_str)

	# Execute topweeks query
	topweeks_result = dbconn.execute(topweeks_query, topweeks_params).fetchone()
	topweeks = topweeks_result[0] if topweeks_result else 0

	return {'medals': medals, 'topweeks': topweeks}


# Convenience wrappers
@cached_wrapper
@connection_provider
def get_artist_medals_and_topweeks(artist_id, associated=True, dbconn=None):
	"""Calculate medals and topweeks for an artist. See get_medals_and_topweeks()."""
	return get_medals_and_topweeks('artist', artist_id, associated=associated, dbconn=dbconn)


@cached_wrapper
@connection_provider
def get_track_medals_and_topweeks(track_id, dbconn=None):
	"""Calculate medals and topweeks for a track. See get_medals_and_topweeks()."""
	return get_medals_and_topweeks('track', track_id, associated=False, dbconn=dbconn)


@cached_wrapper
@connection_provider
def get_album_medals_and_topweeks(album_id, dbconn=None):
	"""Calculate medals and topweeks for an album. See get_medals_and_topweeks()."""
	return get_medals_and_topweeks('album', album_id, associated=False, dbconn=dbconn)



### functions that get mappings for several entities -> rows

@cached_wrapper_individual
@connection_provider
def get_artists_of_tracks(track_ids,dbconn=None):
	"""
	Get artists for tracks, batching queries to avoid SQLite parameter limits.

	SQLite has SQLITE_MAX_VARIABLE_NUMBER limit (default 32766). When IN clauses
	exceed this, performance degrades catastrophically (50k tracks/sec → 400 tracks/sec).
	We batch in chunks of 999 to stay well under the limit.
	"""
	jointable = sql.join(
		DB['trackartists'],
		DB['artists']
	)

	track_ids_list = list(track_ids)
	artists = {}

	# Batch size: 999 is safe (well under 32766 limit)
	# This prevents catastrophic slowdown when track_ids > 32k
	BATCH_SIZE = 999

	for i in range(0, len(track_ids_list), BATCH_SIZE):
		batch = track_ids_list[i:i + BATCH_SIZE]

		# we need to select to avoid multiple 'id' columns that will then
		# be misinterpreted by the row-dict converter
		op = sql.select(
			DB['artists'],
			DB['trackartists'].c.track_id
		).select_from(jointable).where(
			DB['trackartists'].c.track_id.in_(batch)
		)

		result = dbconn.execute(op).all()

		for row in result:
			artists.setdefault(row.track_id,[]).append(artist_db_to_dict(row,dbconn=dbconn))

	return artists

@cached_wrapper_individual
@connection_provider
def get_artists_of_albums(album_ids,dbconn=None):
	"""Batch queries to avoid SQLite parameter limits"""
	jointable = sql.join(
		DB['albumartists'],
		DB['artists']
	)

	album_ids_list = list(album_ids)
	artists = {}
	BATCH_SIZE = 999

	for i in range(0, len(album_ids_list), BATCH_SIZE):
		batch = album_ids_list[i:i + BATCH_SIZE]

		# we need to select to avoid multiple 'id' columns that will then
		# be misinterpreted by the row-dict converter
		op = sql.select(
			DB['artists'],
			DB['albumartists'].c.album_id
		).select_from(jointable).where(
			DB['albumartists'].c.album_id.in_(batch)
		)
		result = dbconn.execute(op).all()

		for row in result:
			artists.setdefault(row.album_id,[]).append(artist_db_to_dict(row,dbconn=dbconn))

	return artists

@cached_wrapper_individual
@connection_provider
def get_albums_of_artists(artist_ids,dbconn=None):
	"""Batch queries to avoid SQLite parameter limits"""
	jointable = sql.join(
		DB['albumartists'],
		DB['albums']
	)

	artist_ids_list = list(artist_ids)
	albums = {}
	BATCH_SIZE = 999

	for i in range(0, len(artist_ids_list), BATCH_SIZE):
		batch = artist_ids_list[i:i + BATCH_SIZE]

		# we need to select to avoid multiple 'id' columns that will then
		# be misinterpreted by the row-dict converter
		op = sql.select(
			DB["albums"],
			DB['albumartists'].c.artist_id
		).select_from(jointable).where(
			DB['albumartists'].c.artist_id.in_(batch)
		)
		result = dbconn.execute(op).all()

		for row in result:
			albums.setdefault(row.artist_id,[]).append(album_db_to_dict(row,dbconn=dbconn))

	return albums

@cached_wrapper_individual
@connection_provider
# this includes the artists' own albums!
def get_albums_artists_appear_on(artist_ids,dbconn=None):

	jointable1 = sql.join(
		DB["trackartists"],
		DB["tracks"]
	)
	jointable2 = sql.join(
		jointable1,
		DB["albums"]
	)

	# we need to select to avoid multiple 'id' columns that will then
	# be misinterpreted by the row-dict converter
	op = sql.select(
		DB["albums"],
		DB["trackartists"].c.artist_id
	).select_from(jointable2).where(
		DB['trackartists'].c.artist_id.in_(artist_ids)
	)
	result = dbconn.execute(op).all()

	albums = {}
	# avoid duplicates from multiple tracks in album by same artist
	already_done = {}
	for row in result:
		if row.id in already_done.setdefault(row.artist_id,[]):
			pass
		else:
			albums.setdefault(row.artist_id,[]).append(album_db_to_dict(row,dbconn=dbconn))
			already_done[row.artist_id].append(row.id)
	return albums


@cached_wrapper_individual
@connection_provider
def get_tracks_map(track_ids,dbconn=None):
	op = DB['tracks'].select().where(
		DB['tracks'].c.id.in_(track_ids)
	)
	result = dbconn.execute(op).all()

	tracks = {}
	result = list(result)
	# this will get a list of artistdicts in the correct order of our rows
	trackdicts = tracks_db_to_dict(result,dbconn=dbconn)

	for row,trackdict in zip(result,trackdicts):
		tracks[row.id] = trackdict

	return tracks

@cached_wrapper_individual
@connection_provider
def get_artists_map(artist_ids,dbconn=None):

	op = DB['artists'].select().where(
		DB['artists'].c.id.in_(artist_ids)
	)
	result = dbconn.execute(op).all()

	artists = {}
	result = list(result)
	# this will get a list of artistdicts in the correct order of our rows
	artistdicts = artists_db_to_dict(result,dbconn=dbconn)
	for row,artistdict in zip(result,artistdicts):
		artists[row.id] = artistdict
	return artists


@cached_wrapper_individual
@connection_provider
def get_albums_map(album_ids,dbconn=None):
	op = DB['albums'].select().where(
		DB['albums'].c.id.in_(album_ids)
	)
	result = dbconn.execute(op).all()

	albums = {}
	result = list(result)
	# this will get a list of albumdicts in the correct order of our rows
	albumdicts = albums_db_to_dict(result,dbconn=dbconn)

	for row,albumdict in zip(result,albumdicts):
		albums[row.id] = albumdict

	return albums

### associations

@cached_wrapper
@connection_provider
def get_associated_artists(*artists,resolve_ids=True,dbconn=None):
	artist_ids = [get_artist_id(a,dbconn=dbconn) for a in artists]

	jointable = sql.join(
		DB['associated_artists'],
		DB['artists'],
		DB['associated_artists'].c.source_artist == DB['artists'].c.id
	)

	# we need to select to avoid multiple 'id' columns that will then
	# be misinterpreted by the row-dict converter
	op = sql.select(
		DB['artists']
	).select_from(jointable).where(
		DB['associated_artists'].c.target_artist.in_(artist_ids)
	)
	result = dbconn.execute(op).all()

	if resolve_ids:
		artists = artists_db_to_dict(result,dbconn=dbconn)
		return artists
	else:
		return [a.id for a in result]

@cached_wrapper
@connection_provider
def get_associated_artist_map(artists=[],artist_ids=None,resolve_ids=True,dbconn=None):

	ids_supplied = (artist_ids is not None)

	if not ids_supplied:
		artist_ids = [get_artist_id(a,dbconn=dbconn) for a in artists]


	jointable = sql.join(
		DB['associated_artists'],
		DB['artists'],
		DB['associated_artists'].c.source_artist == DB['artists'].c.id
	)

	# we need to select to avoid multiple 'id' columns that will then
	# be misinterpreted by the row-dict converter
	op = sql.select(
		DB['artists'],
		DB['associated_artists'].c.target_artist
	).select_from(jointable).where(
		DB['associated_artists'].c.target_artist.in_(artist_ids)
	)
	result = dbconn.execute(op).all()

	artists_to_associated = {a_id:[] for a_id in artist_ids}
	for row in result:
		if resolve_ids:
			artists_to_associated[row.target_artist].append(artists_db_to_dict([row],dbconn=dbconn)[0])
		else:
			artists_to_associated[row.target_artist].append(row.id)

	if not ids_supplied:
		# if we supplied the artists, we want to convert back for the result
		artists_to_associated = {artists[artist_ids.index(k)]:v for k,v in artists_to_associated.items()}

	return artists_to_associated


@cached_wrapper
@connection_provider
def get_credited_artists(*artists,dbconn=None):
	artist_ids = [get_artist_id(a,dbconn=dbconn) for a in artists]

	jointable = sql.join(
		DB['associated_artists'],
		DB['artists'],
		DB['associated_artists'].c.target_artist == DB['artists'].c.id
	)

	# we need to select to avoid multiple 'id' columns that will then
	# be misinterpreted by the row-dict converter
	op = sql.select(
		DB['artists']
	).select_from(jointable).where(
		DB['associated_artists'].c.source_artist.in_(artist_ids)
	)
	result = dbconn.execute(op).all()

	artists = artists_db_to_dict(result,dbconn=dbconn)
	return artists


### get a specific entity by id

@cached_wrapper
@connection_provider
def get_track(track_id: int, dbconn=None) -> TrackDict:
	op = DB['tracks'].select().where(
		DB['tracks'].c.id == track_id
	)
	result = dbconn.execute(op).all()

	trackinfo = result[0]
	return track_db_to_dict(trackinfo, dbconn=dbconn)


@cached_wrapper
@connection_provider
def get_artist(artist_id: int, dbconn=None) -> str:
	op = DB['artists'].select().where(
		DB['artists'].c.id == artist_id
	)
	result = dbconn.execute(op).all()

	artistinfo = result[0]
	return artist_db_to_dict(artistinfo, dbconn=dbconn)


@cached_wrapper
@connection_provider
def get_album(album_id: int, dbconn=None) -> AlbumDict:
	op = DB['albums'].select().where(
		DB['albums'].c.id == album_id
	)
	result = dbconn.execute(op).all()

	albuminfo = result[0]
	return album_db_to_dict(albuminfo, dbconn=dbconn)


@cached_wrapper
@connection_provider
def get_scrobble(timestamp: int, include_internal=False, dbconn=None) -> ScrobbleDict:
	op = DB['scrobbles'].select().where(
		DB['scrobbles'].c.timestamp == timestamp
	)
	result = dbconn.execute(op).all()

	scrobble = result[0]
	return scrobbles_db_to_dict(rows=[scrobble], include_internal=include_internal)[0]


@cached_wrapper
@connection_provider
def search_artist(searchterm,dbconn=None):
	op = DB['artists'].select().where(
		DB['artists'].c.name_normalized.ilike(normalize_name(f"%{searchterm}%"))
	)
	result = dbconn.execute(op).all()

	return [get_artist(row.id,dbconn=dbconn) for row in result]

@cached_wrapper
@connection_provider
def search_track(searchterm,dbconn=None):
	op = DB['tracks'].select().where(
		DB['tracks'].c.title_normalized.ilike(normalize_name(f"%{searchterm}%"))
	)
	result = dbconn.execute(op).all()

	return [get_track(row.id,dbconn=dbconn) for row in result]

@cached_wrapper
@connection_provider
def search_album(searchterm,dbconn=None):
	op = DB['albums'].select().where(
		DB['albums'].c.albtitle_normalized.ilike(normalize_name(f"%{searchterm}%"))
	)
	result = dbconn.execute(op).all()

	return [get_album(row.id,dbconn=dbconn) for row in result]

##### MAINTENANCE

@runhourly
@connection_provider
@no_aux_mode
def clean_db(dbconn=None):

	with SCROBBLE_LOCK:
		log(f"Database Cleanup...")

		to_delete = [
			# NULL associations
			"from albumartists where album_id is NULL",
			"from albumartists where artist_id is NULL",
			"from trackartists where track_id is NULL",
			"from trackartists where artist_id is NULL",
			# tracks with no scrobbles (trackartist entries first)
			"from trackartists where track_id in (select id from tracks where id not in (select track_id from scrobbles))",
			"from tracks where id not in (select track_id from scrobbles)",
			# artists with no tracks AND no albums
			"from artists where id not in (select artist_id from trackartists) \
				and id not in (select target_artist from associated_artists) \
				and id not in (select artist_id from albumartists)",
			# tracks with no artists (scrobbles first)
			"from scrobbles where track_id in (select id from tracks where id not in (select track_id from trackartists))",
			"from tracks where id not in (select track_id from trackartists)",
			# albums with no tracks (albumartist entries first)
			"from albumartists where album_id in (select id from albums where id not in (select album_id from tracks where album_id is not null))",
			"from albums where id not in (select album_id from tracks where album_id is not null)",
			# albumartist entries that are missing a reference
			"from albumartists where album_id not in (select album_id from tracks where album_id is not null)",
			"from albumartists where artist_id not in (select id from artists)",
			# trackartist entries that mare missing a reference
			"from trackartists where track_id not in (select id from tracks)",
			"from trackartists where artist_id not in (select id from artists)"
		]

		for d in to_delete:
			selection = dbconn.execute(sql.text(f"select * {d}"))
			for row in selection.all():
				log(f"Deleting {row}")
			deletion = dbconn.execute(sql.text(f"delete {d}"))

		log("Database Cleanup complete!")



	#if a2+a1>0: log(f"Deleted {a2} tracks without scrobbles ({a1} track artist entries)")

	#if a3>0: log(f"Deleted {a3} artists without tracks")

	#if a5+a4>0: log(f"Deleted {a5} tracks without artists ({a4} scrobbles)")



@runmonthly
@no_aux_mode
def renormalize_names():

	with SCROBBLE_LOCK:
		with engine.begin() as conn:
			rows = conn.execute(DB['artists'].select()).all()

			for row in rows:
				id = row.id
				name = row.name
				norm_actual = row.name_normalized
				norm_target = normalize_name(name)
				if norm_actual != norm_target:
					log(f"{name} should be normalized to {norm_target}, but is instead {norm_actual}, fixing...")

					rows = conn.execute(DB['artists'].update().where(DB['artists'].c.id == id).values(name_normalized=norm_target))


@connection_provider
def merge_duplicate_tracks(artist_id=None,dbconn=None):

	affected_track_conditions = []
	if artist_id:
		affected_track_conditions = [DB['trackartists'].c.artist_id == artist_id]

	rows = dbconn.execute(
		DB['trackartists'].select().where(
			*affected_track_conditions
		)
	)
	affected_tracks = [r.track_id for r in rows]

	track_artists = {}
	rows = dbconn.execute(
		DB['trackartists'].select().where(
			DB['trackartists'].c.track_id.in_(affected_tracks)
		)
	)


	for row in rows:
		track_artists.setdefault(row.track_id,[]).append(row.artist_id)

	artist_combos = {}
	for track_id in track_artists:
		artist_combos.setdefault(tuple(sorted(track_artists[track_id])),[]).append(track_id)

	for c in artist_combos:
		if len(artist_combos[c]) > 1:
			track_identifiers = {}
			for track_id in artist_combos[c]:
				track_identifiers.setdefault(normalize_name(get_track(track_id)['title']),[]).append(track_id)
			for track in track_identifiers:
				if len(track_identifiers[track]) > 1:
					target,*src = track_identifiers[track]
					merge_tracks(target,src,dbconn=dbconn)



@connection_provider
def merge_duplicate_albums(artist_id=None,dbconn=None):

	affected_album_conditions = []
	if artist_id:
		affected_album_conditions = [DB['albumartists'].c.artist_id == artist_id]

	rows = dbconn.execute(
		DB['albumartists'].select().where(
			*affected_album_conditions
		)
	)
	affected_albums = [r.album_id for r in rows]

	album_artists = {}
	rows = dbconn.execute(
		DB['albumartists'].select().where(
			DB['albumartists'].c.album_id.in_(affected_albums)
		)
	)


	for row in rows:
		album_artists.setdefault(row.album_id,[]).append(row.artist_id)

	artist_combos = {}
	for album_id in album_artists:
		artist_combos.setdefault(tuple(sorted(album_artists[album_id])),[]).append(album_id)

	for c in artist_combos:
		if len(artist_combos[c]) > 1:
			album_identifiers = {}
			for album_id in artist_combos[c]:
				album_identifiers.setdefault(normalize_name(get_album(album_id)['albumtitle']),[]).append(album_id)
			for album in album_identifiers:
				if len(album_identifiers[album]) > 1:
					target,*src = album_identifiers[album]
					merge_albums(target,src,dbconn=dbconn)






@connection_provider
def guess_albums(track_ids=None,replace=False,dbconn=None):

	MIN_NUM_TO_ASSIGN = 1

	jointable = sql.join(
		DB['scrobbles'],
		DB['tracks']
	)

	# get all scrobbles of the respective tracks that have some info
	conditions = [
		DB['scrobbles'].c.extra.isnot(None) | DB['scrobbles'].c.rawscrobble.isnot(None)
	]
	if track_ids is not None:
		# only do these tracks
		conditions.append(
			DB['scrobbles'].c.track_id.in_(track_ids)
		)
	if not replace:
		# only tracks that have no album yet
		conditions.append(
			DB['tracks'].c.album_id.is_(None)
		)

	op = sql.select(
		DB['scrobbles']
	).select_from(jointable).where(
		*conditions
	)

	result = dbconn.execute(op).all()

	# for each track, count what album info appears how often
	possible_albums = {}
	for row in result:
		albumtitle, albumartists = None, None
		if row.extra:
			extrainfo = json.loads(row.extra)
			albumtitle = extrainfo.get("album_name") or extrainfo.get("album_title")
			albumartists = extrainfo.get("album_artists",[])
		if not albumtitle:
			# either we didn't have info in the exta col, or there was no albumtitle
			# try the raw scrobble
			extrainfo = json.loads(row.rawscrobble)
			albumtitle = extrainfo.get("album_name") or extrainfo.get("album_title")
			albumartists = albumartists or extrainfo.get("album_artists",[])
		if albumtitle:
			hashable_albuminfo = tuple([*albumartists,albumtitle])
			possible_albums.setdefault(row.track_id,{}).setdefault(hashable_albuminfo,0)
			possible_albums[row.track_id][hashable_albuminfo] += 1

	res = {}
	for track_id in possible_albums:
		options = possible_albums[track_id]
		if len(options)>0:
			# pick the one with most occurences
			mostnum = max(options[albuminfo] for albuminfo in options)
			if mostnum >= MIN_NUM_TO_ASSIGN:
				bestpick = [albuminfo for albuminfo in options if options[albuminfo] == mostnum][0]
				#print("best pick",track_id,bestpick)
				*artists,title = bestpick
				res[track_id] = {"assigned":{
					"artists":artists,
					"albumtitle": title
				}}
				if len(artists) == 0:
					# for albums without artist, assume track artist
					res[track_id]["guess_artists"] = []
			else:
				res[track_id] = {"assigned":False,"reason":"Not enough data"}

		else:
			res[track_id] = {"assigned":False,"reason":"No scrobbles with album information found"}



	missing_artists = [track_id for track_id in res if "guess_artists" in res[track_id]]

	#we're pointlessly getting the albumartist names here even though the IDs would be enough
	#but it's better for function separation I guess
	jointable = sql.join(
		DB['trackartists'],
		DB['artists']
	)
	op = sql.select(
		DB['trackartists'].c.track_id,
		DB['artists']
	).select_from(jointable).where(
		DB['trackartists'].c.track_id.in_(missing_artists)
	)
	result = dbconn.execute(op).all()

	for row in result:
		res[row.track_id]["guess_artists"].append(row.name)

	return res





##### AUX FUNCS



# function to turn the name into a representation that can be easily compared, ignoring minor differences
remove_symbols = ["'","`","’"]
replace_with_space = [" - ",": "]
def normalize_name(name):
	for r in replace_with_space:
		name = name.replace(r," ")
	name = "".join(char for char in unicodedata.normalize('NFD',name.lower())
		if char not in remove_symbols and unicodedata.category(char) != 'Mn')
	return name


def now():
	return int(datetime.now().timestamp())

def rank(ls,key):
	for rnk in range(len(ls)):
		if rnk == 0 or ls[rnk][key] < ls[rnk-1][key]:
			ls[rnk]["rank"] = rnk + 1
		else:
			ls[rnk]["rank"] = ls[rnk-1]["rank"]
	return ls
