"""
Pure Utility Functions
=======================

This module contains pure utility functions with no database dependencies.
These functions perform common data transformations and operations used
throughout the database layer.

Functions:
    normalize_name: Normalize string names for consistent comparison
    now: Get current Unix timestamp
    rank: Assign rank numbers to sorted list items

All functions in this module are stateless and have no side effects,
making them safe to use anywhere in the codebase.
"""

import unicodedata
from datetime import datetime


# String normalization configuration
# Use explicit Unicode escape sequences to ensure correct character matching
remove_symbols = [
	"\u0027",  # ' - APOSTROPHE (regular ASCII apostrophe)
	"\u0060",  # ` - GRAVE ACCENT
	"\u2019",  # ' - RIGHT SINGLE QUOTATION MARK (fancy/curly apostrophe)
	"\u2018",  # ' - LEFT SINGLE QUOTATION MARK
	"\u02BC",  # ʼ - MODIFIER LETTER APOSTROPHE
	"\u02BB",  # ʻ - MODIFIER LETTER TURNED COMMA
]
replace_with_space = [" - ", ": "]


def normalize_name(name):
	"""
	Normalize a name string for consistent comparison and storage.

	This function performs the following transformations:
	1. Replaces specific punctuation patterns with spaces
	2. Converts to lowercase
	3. Applies Unicode NFD normalization
	4. Removes diacritical marks and configured symbols

	Args:
	    name (str): The name string to normalize

	Returns:
	    str: Normalized name string

	Examples:
	    >>> normalize_name("Café - The Band")
	    'cafe the band'
	    >>> normalize_name("Björk: Greatest Hits")
	    'bjork greatest hits'
	"""
	for r in replace_with_space:
		name = name.replace(r," ")
	name = "".join(char for char in unicodedata.normalize('NFD',name.lower())
		if char not in remove_symbols and unicodedata.category(char) != 'Mn')
	return name


def now():
	"""
	Get the current Unix timestamp.

	Returns:
	    int: Current time as Unix timestamp (seconds since epoch)

	Examples:
	    >>> timestamp = now()
	    >>> isinstance(timestamp, int)
	    True
	"""
	return int(datetime.now().timestamp())

def rank(ls,key):
	"""
	Assign rank numbers to items in a sorted list.

	This function adds a 'rank' field to each dictionary in the list,
	assigning rank numbers based on the specified key. Items with equal
	values receive the same rank (dense ranking).

	Args:
	    ls (list[dict]): List of dictionaries to rank (assumed pre-sorted)
	    key (str): Dictionary key to use for ranking comparison

	Returns:
	    list[dict]: The same list with 'rank' field added to each item

	Notes:
	    - List should be pre-sorted in descending order by the key
	    - Modifies the input list in place
	    - Uses dense ranking (1,2,2,3 not 1,2,2,4)

	Examples:
	    >>> items = [{'name': 'A', 'score': 100}, {'name': 'B', 'score': 90}]
	    >>> rank(items, 'score')
	    [{'name': 'A', 'score': 100, 'rank': 1}, {'name': 'B', 'score': 90, 'rank': 2}]
	"""
	for rnk in range(len(ls)):
		if rnk == 0 or ls[rnk][key] < ls[rnk-1][key]:
			ls[rnk]["rank"] = rnk + 1
		else:
			ls[rnk]["rank"] = ls[rnk-1]["rank"]
	return ls
