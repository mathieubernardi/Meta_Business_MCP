"""Exceptions des outils.

Contrat d'erreur : tout échec qui empêche un outil d'aboutir est **levé**. Le SDK
MCP le renvoie alors au client avec `isError: true`, au lieu d'un faux succès
porteur d'une clé `error` que seule la lecture du contenu permettait de repérer.

Les résultats partiels restent des réponses normales : métriques indisponibles
(`errors`), nettoyage best-effort échoué (`cleanup_error`), section vide d'une
synthèse.
"""

from __future__ import annotations

from ._compat import ToolError

_RETRY_LATER = "IN_PROGRESS", "TIMEOUT"


class MetaMCPError(ToolError):
    """Échec anticipé d'un outil.

    Hériter de `ToolError` est indispensable : le SDK MCP (>= 2.2) ne transmet au
    modèle que le message d'un `ToolError`. Toute autre exception est traitée comme
    un plantage, et le modèle ne lit plus que « Error executing tool <nom> ».
    """


class ToolInputError(MetaMCPError, ValueError):
    """Paramètre invalide : rien n'a été envoyé à Meta."""


class OperationError(MetaMCPError, RuntimeError):
    """Une opération Meta a été lancée mais n'a pas abouti."""


class ContainerNotReadyError(OperationError):
    """Un conteneur Instagram n'a pas atteint l'état FINISHED."""

    def __init__(self, container_id: str, status: str) -> None:
        self.container_id = container_id
        self.status = status
        if status in _RETRY_LATER:
            hint = (
                "traitement encore en cours côté Instagram : suis-le avec "
                "ig_container_status, puis publie-le avec ig_publish_container"
            )
        else:
            hint = "contenu refusé ou expiré par Instagram : recrée la publication"
        super().__init__(
            f"conteneur Instagram {container_id} non prêt (statut {status}) — {hint}"
        )

    @property
    def still_processing(self) -> bool:
        """Vrai si Instagram peut encore terminer le traitement du conteneur."""
        return self.status in _RETRY_LATER
