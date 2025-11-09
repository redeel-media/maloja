from ._base import APIHandler
from ._exceptions import *
from .. import database
from ._apikeys import apikeystore

class Audioscrobbler(APIHandler):
	__apiname__ = "Audioscrobbler"
	__doclink__ = "https://www.last.fm/api/scrobbling"
	__aliases__ = [
		"audioscrobbler/2.0",
		"gnufm/2.0",
		"gnukebox/2.0",
	]

	def init(self):

		# no need to save these on disk, clients can always request a new session
		self.mobile_sessions = {}
		self.methods = {
			"auth.getMobileSession":self.authmobile,
			"track.scrobble":self.submit_scrobble
		}
		self.errors = {
			BadAuthException: (400, {"error": 6, "message": "Requires authentication"}),
			InvalidAuthException: (401, {"error": 4, "message": "Invalid credentials"}),
			InvalidMethodException: (200, {"error": 3, "message": "Invalid method"}),
			InvalidSessionKey: (403, {"error": 9, "message": "Invalid session key"}),
			Exception: (500, {"error": 8, "message": "Operation failed"})
		}

	# xml string escaping: https://stackoverflow.com/a/28703510
	def xml_escape(self, str_xml: str):
		str_xml = str_xml.replace("&", "&amp;")
		str_xml = str_xml.replace("<", "&lt;")
		str_xml = str_xml.replace("<", "&lt;")
		str_xml = str_xml.replace("\"", "&quot;")
		str_xml = str_xml.replace("'", "&apos;")
		return str_xml

	def get_method(self,pathnodes,keys):
		return keys.get("method")

	def generate_key(self,client):
		key = "".join(
		    str(
		        random.choice(
		            list(range(10)) + list("abcdefghijklmnopqrstuvwxyz") +
		            list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))) for _ in range(64))

		self.mobile_sessions[key] = client
		return key

	def authmobile(self,pathnodes,keys):
		token = keys.get("authToken")
		user = keys.get("username")
		password = keys.get("password")
		format = keys.get("format") or "xml" # Audioscrobbler 2.0 uses XML by default
		# either username and password
		if user is not None and password is not None:
			client = apikeystore.check_and_identify_key(password)
			if client:
				sessionkey = self.generate_key(client)
				if format == "json":
					return 200,{"session":{"key":sessionkey}}
				else:
					return 200,"""<lfm status="ok">
	<session>
		<name>%s</name>
		<key>%s</key>
		<subscriber>0</subscriber>
	</session>
</lfm>""" % (self.xml_escape(user), self.xml_escape(sessionkey))
			else:
				raise InvalidAuthException()
		# or username and token (deprecated by lastfm)
		elif user is not None and token is not None:
			for client in apikeystore:
				key = apikeystore[client]
				if md5(user + md5(key)) == token:
					sessionkey = self.generate_key(client)
					if format == "json":
						return 200,{"session":{"key":sessionkey}}
					else:
						return 200,"""<lfm status="ok">
	<session>
		<name>%s</name>
		<key>%s</key>
		<subscriber>0</subscriber>
	</session>
</lfm>""" % (self.xml_escape(user), self.xml_escape(sessionkey))
			raise InvalidAuthException()
		else:
			raise BadAuthException()

	def submit_scrobble(self,pathnodes,keys):
		key = keys.get("sk")
		if key is None:
			raise InvalidSessionKey()
		client = self.mobile_sessions.get(key)
		if not client:
			raise InvalidSessionKey()
		if "track" in keys and "artist" in keys:
			# Single scrobble submission
			artiststr,titlestr = keys["artist"], keys["track"]
			try:
				timestamp = int(keys["timestamp"])
			except Exception:
				timestamp = None

			# Build scrobble data
			scrobble_data = {
				'track_artists': [artiststr],
				'track_title': titlestr,
				'scrobble_time': timestamp
			}

			# Extract album information if provided
			if 'album' in keys:
				scrobble_data['album_title'] = keys['album']

			# Extract album artist if provided (Navidrome sends this!)
			if 'albumArtist' in keys:
				scrobble_data['album_artists'] = [keys['albumArtist']]

			self.scrobble(scrobble_data, client=client)
		else:
			# Batch scrobble submission
			for num in range(50):
				if "track[" + str(num) + "]" in keys:
					artiststr = keys["artist[" + str(num) + "]"]
					titlestr = keys["track[" + str(num) + "]"]
					try:
						timestamp = int(keys["timestamp[" + str(num) + "]"])
					except Exception:
						timestamp = None

					# Build scrobble data
					scrobble_data = {
						'track_artists': [artiststr],
						'track_title': titlestr,
						'scrobble_time': timestamp
					}

					# Extract album information if provided
					album_key = "album[" + str(num) + "]"
					if album_key in keys:
						scrobble_data['album_title'] = keys[album_key]

					# Extract album artist if provided
					album_artist_key = "albumArtist[" + str(num) + "]"
					if album_artist_key in keys:
						scrobble_data['album_artists'] = [keys[album_artist_key]]

					self.scrobble(scrobble_data, client=client)

		return 200,{"scrobbles":{"@attr":{"ignored":0}}}


import hashlib
import random

def md5(input):
	m = hashlib.md5()
	m.update(bytes(input,encoding="utf-8"))
	return m.hexdigest()
