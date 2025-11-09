"""
Database Migration Runner for Maloja

Automatically applies SQL migrations on startup:
- Tracks applied migrations in migrations_applied table
- Handles both fresh and existing databases seamlessly
- Creates automatic backups before upgrading existing databases
- Executes migrations in sorted order
- Runs ANALYZE after applying migrations
"""

import os
import glob
import re
from pathlib import Path
from doreah.logging import log
from ..pkg_global.conf import data_dir
import sqlalchemy as sql


def get_migrations_folder():
	"""Get the path to the migrations folder"""
	db_module = Path(__file__).parent
	maloja_package = db_module.parent  
	migrations_folder = maloja_package / 'migrations'
	return migrations_folder


def create_migrations_table(engine):
	"""
	Create migrations_applied table if it doesn't exist

	This table tracks which migrations have been applied to avoid re-running them.
	"""
	create_table_sql = """
	CREATE TABLE IF NOT EXISTS migrations_applied (
		migration_name TEXT PRIMARY KEY,
		applied_at INTEGER NOT NULL
	);
	"""

	with engine.connect() as conn:
		conn.execute(sql.text(create_table_sql))
		conn.commit()


def get_applied_migrations(engine):
	"""
	Get list of migrations that have already been applied

	Returns:
		set: Set of migration filenames that have been applied
	"""
	with engine.connect() as conn:
		# Check if table exists first
		result = conn.execute(sql.text(
			"SELECT name FROM sqlite_master WHERE type='table' AND name='migrations_applied'"
		))
		if not result.fetchone():
			return set()

		# Get all applied migrations
		result = conn.execute(sql.text("SELECT migration_name FROM migrations_applied"))
		return {row[0] for row in result.fetchall()}


def is_existing_database(engine):
	"""
	Check if this is an existing database with data (vs fresh install)

	Returns:
		bool: True if database has scrobbles, False otherwise
	"""
	with engine.connect() as conn:
		# Check if scrobbles table exists
		result = conn.execute(sql.text(
			"SELECT name FROM sqlite_master WHERE type='table' AND name='scrobbles'"
		))
		if not result.fetchone():
			return False

		# Check if it has any data
		result = conn.execute(sql.text("SELECT COUNT(*) FROM scrobbles"))
		count = result.fetchone()[0]
		return count > 0


def validate_migration_filename(filename):
	"""
	Validate migration filename follows expected pattern

	Expected pattern: NNN_description.sql where NNN is a 3-digit number

	Args:
		filename: Name of migration file

	Returns:
		tuple: (is_valid, error_message)
	"""
	# Check file extension
	if not filename.endswith('.sql'):
		return False, f"Migration file must have .sql extension: {filename}"

	# Check pattern: 3 digits, underscore, description
	if not re.match(r'^\d{3}_\w+\.sql$', filename):
		return False, f"Migration file must match pattern NNN_description.sql: {filename}"

	return True, None


def column_exists(conn, table_name, column_name):
	"""
	Check if a column exists in a table

	Args:
		conn: SQLAlchemy connection
		table_name: Name of the table
		column_name: Name of the column to check

	Returns:
		bool: True if column exists, False otherwise
	"""
	result = conn.execute(
		sql.text("SELECT COUNT(*) as count FROM pragma_table_info(:table) WHERE name = :column"),
		{"table": table_name, "column": column_name}
	).fetchone()
	return result.count > 0


def get_available_migrations():
	"""
	Get list of all available migration files

	Returns:
		list: Sorted list of (filepath, filename) tuples
	"""
	migrations_folder = get_migrations_folder()

	if not migrations_folder.exists():
		log(f"Migrations folder not found: {migrations_folder}")
		return []

	# Find all .sql files in migrations folder
	migration_files = list(migrations_folder.glob('*.sql'))

	# Validate and filter migration files
	valid_migrations = []
	for f in migration_files:
		is_valid, error_msg = validate_migration_filename(f.name)
		if is_valid:
			valid_migrations.append(f)
		else:
			log(f"Warning: Skipping invalid migration file: {error_msg}")

	# Sort by filename (should be numbered like 001_xxx.sql, 002_xxx.sql)
	valid_migrations.sort(key=lambda p: p.name)

	return [(str(f), f.name) for f in valid_migrations]


def execute_migration(engine, migration_path, migration_name):
	"""
	Execute a single migration file

	Migration files can contain multiple statements separated by semicolons.
	They may also contain sqlite3 dot commands which we need to filter out.

	Args:
		engine: SQLAlchemy engine
		migration_path: Full path to migration file
		migration_name: Name of migration file
	"""
	log(f"Applying migration: {migration_name}")

	try:
		with open(migration_path, 'r', encoding='utf-8') as f:
			sql_content = f.read()

		# Filter out sqlite3 dot commands (like .mode, .headers, .width)
		# These are CLI-specific and not valid SQL
		lines = sql_content.split('\n')
		filtered_lines = []
		for line in lines:
			stripped = line.strip()
			# Skip dot commands but keep SQL
			if stripped.startswith('.'):
				continue
			filtered_lines.append(line)

		sql_content = '\n'.join(filtered_lines)

		# Split into individual statements
		# Use a simpler approach: split by semicolon and clean up
		# SQLite's text() function handles multi-line statements correctly
		raw_statements = sql_content.split(';')
		statements = []

		for stmt in raw_statements:
			# Clean statement: strip whitespace and remove comments
			lines = stmt.split('\n')
			cleaned_lines = []
			for line in lines:
				# Remove inline comments (but preserve -- in strings is okay, SQLite handles it)
				line = line.strip()
				if line and not line.startswith('--'):
					cleaned_lines.append(line)

			statement = '\n'.join(cleaned_lines).strip()
			if statement:
				statements.append(statement)

		# Separate PRAGMA statements from regular SQL
		# PRAGMAs must execute outside transactions (auto-commit mode)
		pragma_statements = []
		regular_statements = []

		for statement in statements:
			# Skip empty statements and comments
			if not statement or statement.startswith('--'):
				continue

			# Skip ALL SELECT statements - they're only for display/verification in sqlite3 CLI
			# They don't modify the database and may use functions unavailable in older SQLite versions
			if statement.upper().startswith('SELECT'):
				continue

			statement_upper = statement.upper().strip()

			# PRAGMA and ANALYZE must run outside transaction
			if statement_upper.startswith(('PRAGMA', 'ANALYZE', 'VACUUM')):
				pragma_statements.append(statement)
			else:
				regular_statements.append(statement)

		# Execute PRAGMA statements first (outside transaction, auto-commit mode)
		if pragma_statements:
			with engine.connect() as conn:
				conn.execution_options(isolation_level="AUTOCOMMIT")
				for statement in pragma_statements:
					try:
						conn.execute(sql.text(statement))
						log(f"  Executed PRAGMA: {statement[:60]}...")
					except Exception as e:
						# PRAGMAs can fail in certain contexts, log but continue
						log(f"  Warning: PRAGMA failed (non-critical): {e}")

		# Execute regular SQL statements in a single transaction
		# This ensures all-or-nothing behavior - if any statement fails,
		# the entire migration is rolled back automatically
		if regular_statements:
			try:
				with engine.begin() as conn:  # Auto-rollback on exception
					for statement in regular_statements:
						# Check if this is an ALTER TABLE ADD COLUMN statement
						# These are not idempotent in SQLite, so we need to check first
						statement_upper = statement.upper().strip()
						if 'ALTER TABLE' in statement_upper and 'ADD COLUMN' in statement_upper:
							# Parse table and column name from statement
							# Pattern: ALTER TABLE table_name ADD COLUMN column_name ...
							import re
							match = re.search(r'ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)', statement, re.IGNORECASE)
							if match:
								table_name = match.group(1)
								column_name = match.group(2)

								# Check if column already exists
								if column_exists(conn, table_name, column_name):
									log(f"  Skipping: Column {table_name}.{column_name} already exists")
									continue
								else:
									log(f"  Adding column: {table_name}.{column_name}")

						conn.execute(sql.text(statement))
				log(f"Successfully applied migration: {migration_name}")
			except Exception as e:
				log(f"CRITICAL: Migration {migration_name} failed and was rolled back")
				log(f"Error: {e}")
				raise RuntimeError(f"Migration {migration_name} failed: {e}") from e
		else:
			log(f"Successfully applied migration: {migration_name} (PRAGMA only)")

	except Exception as e:
		log(f"Error applying migration {migration_name}: {e}")
		raise


def record_migration(engine, migration_name):
	"""
	Record that a migration has been applied

	Args:
		engine: SQLAlchemy engine
		migration_name: Name of migration file
	"""
	import time

	with engine.connect() as conn:
		conn.execute(
			sql.text("INSERT INTO migrations_applied (migration_name, applied_at) VALUES (:name, :time)"),
			{"name": migration_name, "time": int(time.time())}
		)
		conn.commit()


def run_analyze(engine):
	"""
	Run ANALYZE to update query planner statistics

	This is critical after adding indexes or modifying schema.
	"""
	log("Running ANALYZE to update query planner statistics...")

	with engine.connect() as conn:
		conn.execute(sql.text("ANALYZE"))
		conn.commit()

	log("ANALYZE complete")


def check_and_run_backfill(engine):
	"""
	Legacy function - aggregation tables removed

	This function is no longer needed after migration 002 was simplified.
	Aggregation tables were removed after A/B testing showed no benefit.

	Homepage cache populates automatically on first page load.

	Args:
		engine: SQLAlchemy engine (ignored)
	"""
	# No-op - aggregation tables removed
	pass


def run_migrations(engine):
	"""
	Main migration runner function

	This is called on server startup to apply any pending migrations.

	Process:
	1. Create migrations_applied table if needed
	2. Create backup before applying migrations (existing databases only)
	3. Get list of available and applied migrations
	4. Execute unapplied migrations in order
	5. Record each migration as applied
	6. Run ANALYZE if any migrations were applied

	Args:
		engine: SQLAlchemy engine
	"""
	log("Checking for pending database migrations...")

	# Create migrations tracking table
	create_migrations_table(engine)

	# Get migrations
	available_migrations = get_available_migrations()
	applied_migrations = get_applied_migrations(engine)

	# Find unapplied migrations
	unapplied = [
		(path, name) for path, name in available_migrations
		if name not in applied_migrations
	]

	if not unapplied:
		log("No pending migrations")
		return

	log(f"Found {len(unapplied)} pending migration(s)")

	# Create backup before applying migrations (only for existing databases with data)
	# For fresh databases, there's no data to back up
	existing_db = is_existing_database(engine)
	if existing_db:
		# CRITICAL: Backup is MANDATORY for existing databases - migrations are aborted if backup fails
		log("Creating backup before applying migrations...")
		try:
			from ..proccontrol.tasks.backup import backup
			backup_file = backup(targetfolder=data_dir['backups'](), include_images=False)
			log(f"Backup created successfully: {backup_file}")
		except Exception as e:
			log(f"CRITICAL: Cannot create backup before migrations!")
			log(f"Error: {e}")
			log(f"ABORTING migrations to prevent data loss")
			log(f"Migrations will be retried on next startup")
			raise RuntimeError(f"Migration aborted: backup failed ({e})") from e
	else:
		log("Fresh database - skipping backup (no data yet)")

	# Apply each migration
	migrations_applied = False
	for migration_path, migration_name in unapplied:
		try:
			execute_migration(engine, migration_path, migration_name)
			record_migration(engine, migration_name)
			migrations_applied = True
		except Exception as e:
			log(f"Failed to apply migration {migration_name}: {e}")
			log("Stopping migration process - please fix the error and restart")
			raise

	# Run ANALYZE after applying migrations
	if migrations_applied:
		run_analyze(engine)

		# Check if aggregation tables need backfilling
		check_and_run_backfill(engine)

		log("All migrations applied successfully")


def list_migrations(engine):
	"""
	List all migrations and their status (for debugging/info)

	Args:
		engine: SQLAlchemy engine

	Returns:
		list: List of dicts with migration info
	"""
	available = get_available_migrations()
	applied = get_applied_migrations(engine)

	result = []
	for path, name in available:
		result.append({
			'name': name,
			'path': path,
			'applied': name in applied
		})

	return result
