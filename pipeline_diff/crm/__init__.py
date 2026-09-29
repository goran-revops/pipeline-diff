"""CRM clients. Each turns its CRM into the same deals, history, pipelines, and owners lists."""


class CRMError(Exception):
    """A CRM call failed. The message never contains a key."""
