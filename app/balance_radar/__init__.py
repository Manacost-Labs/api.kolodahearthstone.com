"""Balance Radar: nerf and buff candidates from statistics and social signals.

The radar runs on a timer (``python -m app.balance_radar run``), never per
request. One run per format joins three inputs:

* statistics already published by hs-data-api (HSGuru archetypes by period,
  including the ``*_ban`` window, and HSReplay card periods);
* Reddit posts and comments (RedditAPIs) and X posts (GetXAPI);
* a Claude analysis with a structured-output schema.

Every run is stored with its usage and cost; the REST router serves the latest
successful run and the run history.
"""
