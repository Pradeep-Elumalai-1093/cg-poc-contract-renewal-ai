class IngestError(Exception):
    """A problem with the extract or the configuration that should stop the load
    (and leave whatever is live exactly as it was)."""
