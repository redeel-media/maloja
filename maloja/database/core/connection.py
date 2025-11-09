"""
Database Connection Management
===============================

This module manages SQLAlchemy engine configuration, connection lifecycle,
and SQLite optimization settings for the Maloja database.

ENGINE CONFIGURATION:
---------------------

The SQLAlchemy engine is configured specifically for SQLite in a single-user
scenario with the following optimizations:

1. NullPool: No connection pooling
   - SQLite file-based locking makes pooling unnecessary
   - Reduces overhead from pool maintenance
   - Suitable for single-user applications

2. check_same_thread=False: Allow cross-thread connection sharing
   - Python's sqlite3 module normally restricts connections to creating thread
   - Maloja's architecture requires connections to be passed between threads
   - Safe because we use SCROBBLE_LOCK for write coordination

3. WAL Mode (Write-Ahead Logging):
   - Allows concurrent readers and writers
   - Improves performance for read-heavy workloads
   - Set once at database initialization

4. Persistent PRAGMAs:
   - journal_mode=WAL: Enables Write-Ahead Logging
   - synchronous=NORMAL: Reduces fsync frequency (balanced safety/speed)

5. Per-Connection PRAGMAs:
   - foreign_keys=ON: Enable referential integrity checks
   - temp_store=MEMORY: Store temporary tables in RAM (faster)
   - cache_size=-200000: 200MB cache (tuned for artist page performance)
   - wal_autocheckpoint=1000: Auto-checkpoint WAL every 1000 pages

CONNECTION PROVIDER PATTERN:
----------------------------

The connection_provider decorator ensures all database functions can work
both standalone (create their own connection) and within a transaction
(use provided connection):

```python
@connection_provider
def some_function(data, dbconn=None):
    # Always has valid dbconn here
    dbconn.execute(...)
```

Usage patterns:
- Standalone: `some_function(data)` - creates/closes connection automatically
- Transactional: `some_function(data, dbconn=conn)` - reuses existing connection
- Request-scoped: JinjaDBConnection wraps connection for web requests

MALOJA INFO FUNCTIONS:
----------------------

get_maloja_info() and set_maloja_info() provide key-value storage in the
_maloja table for application metadata:

- Database version
- Migration timestamps
- Configuration overrides
- Application state

These functions are used by:
- conf.py: Store/retrieve persistent settings
- migrations.py: Track applied migrations
- Various modules: Store runtime metadata

DESIGN DECISIONS:
-----------------

1. Engine is module-level singleton
   - Created once at import time
   - Shared across all database operations
   - Thread-safe via SQLAlchemy's connection pooling

2. PRAGMA settings split into persistent vs per-connection
   - Persistent: Set once, survives restarts (WAL mode, synchronous)
   - Per-connection: Set on every connection (foreign_keys, cache_size)

3. connection_provider wraps functions, not classes
   - Simpler than context managers for this use case
   - Preserves function signatures
   - Allows optional dbconn parameter pattern

IMPORTS AND DEPENDENCIES:
-------------------------

This module depends on:
- core.schema: DBTABLES, DB, meta (for get/set_maloja_info)
- pkg_global.conf: data_dir (for database file path)

This module is imported by:
- sqldb.py: Re-exports for backward compatibility
- All database modules: Use engine and connection_provider
"""

import sqlalchemy as sql
from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlalchemy.dialects.sqlite import insert as sqliteinsert
from functools import wraps

from ...pkg_global.conf import data_dir
from .schema import DB


# ============================================================================
# ENGINE CONFIGURATION
# ============================================================================

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


# ============================================================================
# PER-CONNECTION PRAGMA CONFIGURATION
# ============================================================================

# Configure SQLite PRAGMAs on every connection
# These are transient settings that must be set per connection
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
	"""
	Set SQLite PRAGMA settings on each connection.

	These optimize SQLite for single-user, read-heavy workloads:
	- foreign_keys: Enable referential integrity checks
	- temp_store: Store temp tables/indexes in memory (faster)
	- cache_size: 200MB cache (increased for artist page performance)
	- wal_autocheckpoint: Auto-checkpoint WAL every 1000 pages
	"""
	cursor = dbapi_conn.cursor()
	cursor.execute("PRAGMA foreign_keys=ON")
	cursor.execute("PRAGMA temp_store=MEMORY")
	cursor.execute("PRAGMA cache_size=-200000")  # 200MB in kilobytes (increased from 64MB)
	cursor.execute("PRAGMA wal_autocheckpoint=1000")
	cursor.close()


# ============================================================================
# CONNECTION PROVIDER DECORATOR
# ============================================================================

def connection_provider(func):
	"""
	Decorator that provides database connection management.

	Allows functions to work both standalone (create their own connection)
	and within transactions (use provided connection).

	The decorated function can be called in two ways:
	1. Without dbconn: Creates a connection and transaction automatically
	2. With dbconn: Uses the provided connection (no new transaction)

	Args:
		func: Function to decorate (must accept dbconn kwarg)

	Returns:
		Wrapped function that manages connection lifecycle

	Example:
		@connection_provider
		def add_scrobble(scrobble_data, dbconn=None):
		    # dbconn is always valid here
		    dbconn.execute(...)

		# Usage 1: Standalone (auto-connection)
		add_scrobble(data)

		# Usage 2: Within transaction
		with engine.connect() as conn:
		    with conn.begin():
		        add_scrobble(data, dbconn=conn)
	"""
	@wraps(func)
	def wrapper(*args, **kwargs):
		if kwargs.get("dbconn") is not None:
			return func(*args, **kwargs)
		else:
			with engine.connect() as connection:
				with connection.begin():
					kwargs['dbconn'] = connection
					return func(*args, **kwargs)

	wrapper.__innerfunc__ = func
	return wrapper


# ============================================================================
# MALOJA INFO FUNCTIONS
# ============================================================================

@connection_provider
def get_maloja_info(keys, dbconn=None):
	"""
	Retrieve application metadata from the _maloja key-value table.

	This function is used to read persistent configuration and state:
	- Database version
	- Migration timestamps
	- Configuration overrides
	- Application runtime state

	Args:
		keys: List of keys to retrieve
		dbconn: Database connection (provided by decorator)

	Returns:
		Dictionary mapping keys to values (only includes found keys)

	Example:
		>>> get_maloja_info(['db_version', 'last_backup'])
		{'db_version': '2', 'last_backup': '2025-11-05'}
	"""
	op = DB['_maloja'].select().where(
		DB['_maloja'].c.key.in_(keys)
	)
	result = dbconn.execute(op).all()

	info = {}
	for row in result:
		info[row.key] = row.value
	return info


@connection_provider
def set_maloja_info(info, dbconn=None):
	"""
	Store application metadata in the _maloja key-value table.

	Uses SQLite's INSERT OR REPLACE (via on_conflict_do_update) to
	upsert key-value pairs.

	This function is used to write persistent configuration and state:
	- Database version (during migrations)
	- Migration timestamps
	- Configuration overrides
	- Application runtime state

	Args:
		info: Dictionary of key-value pairs to store
		dbconn: Database connection (provided by decorator)

	Example:
		>>> set_maloja_info({'db_version': '2', 'last_backup': '2025-11-05'})
	"""
	for k in info:
		op = sqliteinsert(DB['_maloja']).values(
			key=k, value=info[k]
		).on_conflict_do_update(
			index_elements=['key'],
			set_={'value': info[k]}
		)
		dbconn.execute(op)
