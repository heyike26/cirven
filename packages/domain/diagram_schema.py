"""Compatibility imports of the one authoritative discovery graph protocol."""
from packages.domain.discovery_schema import DiscoveryGraph, DiagramNode, DiagramEdge, RevisionRef

ArtifactRef = RevisionRef

__all__ = ['DiscoveryGraph', 'DiagramNode', 'DiagramEdge', 'ArtifactRef']
