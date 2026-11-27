# Saved Airbnb responses (sanitized)

Shapes returned by pyairbnb (`search_all`, `get_calendar(return_dataclass=False)`,
`get_metadata_from_url`, `get_reviews`) with synthetic ids, names and coordinates, and no
personal data or review text. The awkward cases are there on purpose: missing fields,
duplicates, partial rows and changed structures.

Before adopting a new pyairbnb version: `uv lock --upgrade-package pyairbnb && uv sync`,
then `uv run pytest`. `test_collector_contract.py` also checks that pyairbnb still accepts
the arguments this project passes.
