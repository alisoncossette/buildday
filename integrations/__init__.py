"""Stead integrations — thin, swappable adapters to live services.

Each adapter is offline-first: Stead's default demo path never imports these, and a missing key
raises a clear error pointing back to the in-memory fallback. Inject one explicitly to go live.

- bolo_client.BoloClient — live Bolo MCP (@bolospot/mcp) consent layer for ConsentEngine(bolo=...).
"""
