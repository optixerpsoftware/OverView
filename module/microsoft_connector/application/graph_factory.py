from shared_kernel.api import Graph


def build_graph(connector) -> Graph:
    """Construit un client Graph (mode Application) depuis une entité/modèle connecteur."""
    g = Graph()
    g.tenant_id = str(connector.tenant_id)
    g.client_id = str(connector.client_id)
    g.client_secret = connector.client_secret
    g.username = g.password = None  # jamais de mode délégué par connecteur
    return g
