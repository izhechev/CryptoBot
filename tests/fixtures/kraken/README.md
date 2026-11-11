# Kraken fixtures (M1)

These JSON files are HAND-WRITTEN to Kraken's documented public response shape.
They are NOT recorded from the live API, so the values (prices, minimum order
sizes, tick sizes) are illustrative. Tests use them to check parsing and rules
logic, not to assert what Kraken currently lists.

To replace them with real recordings, run on a machine with network access:

    python -m backend.tools.venue_check --save-fixtures /some/dir

and copy the saved files over these, keeping the file names.

No test in the suite contacts the network. The fixtures are read by path or
passed to httpx.MockTransport.
