-- ============================================================================
-- MALOJA DATABASE INITIALIZATION
-- Migration 000: Core Schema Definition
-- ============================================================================
--
-- PURPOSE: Initial database schema for fresh Maloja installations
-- SAFETY: Idempotent (CREATE TABLE IF NOT EXISTS)
--
-- TABLES:
--   _maloja          - Configuration/metadata key-value store
--   scrobbles        - Core listening history table
--   tracks           - Track metadata
--   artists          - Artist metadata
--   albums           - Album metadata
--   trackartists     - Many-to-many track-artist relationships
--   albumartists     - Many-to-many album-artist relationships
--   associated_artists - Artist merge/alias rules
--
-- ============================================================================

-- ============================================================================
-- TABLE: _maloja (Configuration Store)
-- ============================================================================

CREATE TABLE IF NOT EXISTS _maloja (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- ============================================================================
-- TABLE: scrobbles (Core Listening History)
-- ============================================================================

CREATE TABLE IF NOT EXISTS scrobbles (
    timestamp INTEGER PRIMARY KEY,
    rawscrobble TEXT,
    origin TEXT,
    duration INTEGER,
    track_id INTEGER,
    extra TEXT,
    FOREIGN KEY (track_id) REFERENCES tracks(id)
);

-- ============================================================================
-- TABLE: tracks (Track Metadata)
-- ============================================================================

CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    title_normalized TEXT,
    length INTEGER,
    album_id INTEGER,
    FOREIGN KEY (album_id) REFERENCES albums(id)
);

-- ============================================================================
-- TABLE: artists (Artist Metadata)
-- ============================================================================

CREATE TABLE IF NOT EXISTS artists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    name_normalized TEXT
);

-- ============================================================================
-- TABLE: albums (Album Metadata)
-- ============================================================================

CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    albtitle TEXT,
    albtitle_normalized TEXT
);

-- ============================================================================
-- TABLE: trackartists (Many-to-Many: Track-Artist Junction)
-- ============================================================================

CREATE TABLE IF NOT EXISTS trackartists (
    id INTEGER PRIMARY KEY,
    artist_id INTEGER,
    track_id INTEGER,
    FOREIGN KEY (artist_id) REFERENCES artists(id),
    FOREIGN KEY (track_id) REFERENCES tracks(id),
    UNIQUE (artist_id, track_id)
);

-- ============================================================================
-- TABLE: albumartists (Many-to-Many: Album-Artist Junction)
-- ============================================================================

CREATE TABLE IF NOT EXISTS albumartists (
    id INTEGER PRIMARY KEY,
    artist_id INTEGER,
    album_id INTEGER,
    FOREIGN KEY (artist_id) REFERENCES artists(id),
    FOREIGN KEY (album_id) REFERENCES albums(id),
    UNIQUE (artist_id, album_id)
);

-- ============================================================================
-- TABLE: associated_artists (Artist Merge/Alias Rules)
-- ============================================================================

CREATE TABLE IF NOT EXISTS associated_artists (
    source_artist INTEGER,
    target_artist INTEGER,
    FOREIGN KEY (source_artist) REFERENCES artists(id),
    FOREIGN KEY (target_artist) REFERENCES artists(id),
    UNIQUE (source_artist, target_artist)
);

-- ============================================================================
-- INITIALIZATION COMPLETE
-- ============================================================================