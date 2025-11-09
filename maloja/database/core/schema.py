"""
Database Schema Definition
===========================

This module defines the SQLAlchemy schema for the Maloja scrobbling database.
It contains the DBTABLES dictionary, metadata objects, and table creation logic.

SCHEMA STRUCTURE:
-----------------

The database schema follows a star schema design optimized for time-series queries:

FACT TABLE:
  - scrobbles: Central fact table storing individual play events
    - Indexed heavily on timestamp for time-range queries
    - Foreign key to tracks table
    - Contains origin, duration, and extra metadata

DIMENSION TABLES:
  - tracks: Track metadata (title, length, album relationship)
    - Normalized title for deduplication
    - Foreign key to albums

  - artists: Artist metadata (name)
    - Normalized name for deduplication

  - albums: Album metadata (title)
    - Normalized title for deduplication

JUNCTION TABLES:
  - trackartists: Many-to-many relationship between tracks and artists
    - Heavily indexed for N+1 query prevention

  - albumartists: Many-to-many relationship between albums and artists
    - Indexed for album-artist lookups

  - associated_artists: Artist merge/association rules
    - Maps source artists to target artists for query expansion

METADATA TABLE:
  - _maloja: Key-value store for application metadata
    - Stores version info, timestamps, configuration

IMPORTANT NOTES:
----------------

1. DBTABLES is the SQLAlchemy ORM schema reference
   - NOT the source of truth for table creation
   - Used ONLY to generate SQLAlchemy Table() objects

2. Actual table creation is handled by migrations:
   - migrations/000_init.sql: Creates all tables
   - migrations/001_database_optimization.sql: Creates indexes
   - Future migrations: Handle schema changes

3. Migration workflow:
   - Fresh database: All migrations run in order
   - Existing database: Only unapplied migrations run
   - create_tables() runs migrations at import time

4. Making schema changes:
   - Update DBTABLES dict (for ORM)
   - Create new migration SQL file
   - Migration handles actual database ALTER/CREATE statements

5. Performance indexes:
   - All performance-critical indexes defined in migrations
   - DBTABLES 'indexes' list is documentary only
   - Actual index creation: migrations/001_database_optimization.sql

DO NOT:
-------
- Create tables from DBTABLES (use migrations instead)
- Duplicate migration SQL in Python code
- Treat DBTABLES as source of truth (migrations are source of truth)

DESIGN DECISIONS:
-----------------

The schema is optimized for:
- Time-range scrobble queries (timestamp indexes)
- Artist/track/album relationship lookups (junction table indexes)
- Deduplication (normalized name indexes)
- Artist page performance (cache-size pragma, WAL mode)
"""

import sqlalchemy as sql
from doreah.logging import log


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


# DB dict: Maps table names to SQLAlchemy Table objects
# Populated by create_tables() at import time
DB = {}

# SQLAlchemy metadata object
# Used to bind all Table objects together
meta = sql.MetaData()


def create_tables(engine):
	"""
	Create SQLAlchemy Table objects and run database migrations.

	This function performs three critical operations at import time:

	1. Creates SQLAlchemy Table objects from DBTABLES
	   - Populates the global DB dict with Table references
	   - Binds all tables to the metadata object
	   - Used by ORM for query building and relationship mapping

	2. Runs database migrations
	   - Ensures all tables exist before any code tries to use them
	   - Handles chicken-and-egg problem where conf.py writes to _maloja table during import
	   - Applies unapplied migrations in order

	3. Upgrades old databases with new columns
	   - Attempts to add any missing columns via ALTER TABLE
	   - Safely ignores columns that already exist
	   - Provides backward compatibility for databases created before migrations

	IMPORTANT: This function is called at import time to ensure tables exist
	before any application code tries to use them.

	Args:
		engine: SQLAlchemy engine connected to the database

	Side Effects:
		- Populates global DB dict with Table objects
		- Runs database migrations via migrations.run_migrations()
		- May execute ALTER TABLE statements on old databases
		- Logs column additions
	"""
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
	from .. import migrations
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
