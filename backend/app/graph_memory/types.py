"""Tipos de datos del motor de memoria en grafos.

``EntityModel``, ``EdgeModel``, ``EntityText`` y ``EntityEdgeSourceTarget``
describen la ontología; ``BatchAddItem`` describe un fragmento de texto enviado
a un lote de ingesta. Son los únicos tipos que el resto del backend importa, y
conservan los nombres y campos que ya usaba el proyecto.

Una ontología se guarda como definiciones serializables para poder reconstruirla
en cada ingesta (también durante la simulación, mucho después de crearse).
"""

from __future__ import annotations

import keyword
import re
from dataclasses import dataclass
from typing import Any, Optional

from pydantic import BaseModel, Field

# El proyecto anterior trataba los atributos de una entidad como texto.
EntityText = str


class EntityModel(BaseModel):
    """Tipo de entidad de la ontología. Sus campos son los atributos a extraer."""


class EdgeModel(BaseModel):
    """Tipo de relación de la ontología. Sus campos son los atributos a extraer."""


class EntityEdgeSourceTarget(BaseModel):
    """Par (tipo de origen, tipo de destino) válido para un tipo de relación."""

    source: str = "Entity"
    target: str = "Entity"


@dataclass
class BatchAddItem:
    """Un episodio de texto pendiente de agregar a un grafo."""

    type: str = "graph_episode"
    graph_id: str | None = None
    data: str | None = None
    data_type: str = "text"
    source_description: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class SearchFilters:
    """Filtros opcionales de búsqueda."""

    node_labels: list[str] | None = None
    edge_types: list[str] | None = None
    valid_at: str | None = None
    invalid_at: str | None = None
    created_at: str | None = None
    expired_at: str | None = None
    edge_uuids: list[str] | None = None
    edge_source_node_uuids: list[str] | None = None
    edge_target_node_uuids: list[str] | None = None


# Campos internos de los nodos de la base de grafos: no pueden usarse como
# atributo de un tipo de la ontología (Graphiti rechaza la colisión).
RESERVED_FIELD_NAMES = frozenset(
    {
        "uuid",
        "name",
        "labels",
        "group_id",
        "graph_id",
        "name_embedding",
        "summary",
        "attributes",
        "created_at",
    }
)

_INVALID_IDENTIFIER = re.compile(r"[^0-9A-Za-z_]")
_WORD_SPLIT = re.compile(r"[^0-9A-Za-z]+")
_FALLBACK_ATTRIBUTE_NAME = "details"


def field_name(raw: Any) -> str:
    """Convierte un nombre de atributo en un identificador de campo válido."""

    name = _INVALID_IDENTIFIER.sub("_", str(raw or "").strip())
    if not name:
        name = _FALLBACK_ATTRIBUTE_NAME
    if name[0].isdigit():
        name = f"attr_{name}"
    if name in RESERVED_FIELD_NAMES or keyword.iskeyword(name):
        name = f"{name}_"
    return name


def class_name(type_name: str) -> str:
    """Nombre de clase válido para un tipo de la ontología."""

    parts = [part for part in _WORD_SPLIT.split(str(type_name or "")) if part]
    name = "".join(part[:1].upper() + part[1:] for part in parts) or "EntityType"
    if name[0].isdigit():
        name = f"T{name}"
    return name


def _attributes_of(model: type[BaseModel]) -> dict[str, str]:
    """Atributos (campo -> descripción) declarados por un tipo de la ontología."""

    attributes: dict[str, str] = {}
    for name, info in model.model_fields.items():
        if name in RESERVED_FIELD_NAMES:
            continue
        attributes[name] = str(info.description or name)
    return attributes


def entity_definition(type_name: str, model: type[BaseModel]) -> dict[str, Any]:
    """Definición serializable de un tipo de entidad."""

    return {
        "name": type_name,
        "description": str(model.__doc__ or f"A {type_name} entity.").strip(),
        "attributes": _attributes_of(model),
    }


def edge_definition(
    type_name: str,
    model: type[BaseModel],
    source_targets: list[EntityEdgeSourceTarget] | None,
) -> dict[str, Any]:
    """Definición serializable de un tipo de relación."""

    return {
        "name": type_name,
        "description": str(model.__doc__ or f"A {type_name} relationship.").strip(),
        "attributes": _attributes_of(model),
        "source_targets": [
            [str(pair.source or "Entity"), str(pair.target or "Entity")]
            for pair in (source_targets or [])
        ],
    }


def model_from_definition(definition: dict[str, Any]) -> type[BaseModel]:
    """Reconstruye el modelo de un tipo a partir de su definición serializada."""

    type_name = str(definition.get("name") or "EntityType")
    description = str(definition.get("description") or "").strip() or f"A {type_name}."
    attributes = definition.get("attributes")
    if not isinstance(attributes, dict) or not attributes:
        # Sin atributos declarados se pide un campo de detalle genérico.
        attributes = {_FALLBACK_ATTRIBUTE_NAME: f"Additional details about this {type_name}."}

    namespace: dict[str, Any] = {
        "__doc__": description,
        "__annotations__": {},
    }
    used: set[str] = set()
    for raw_name, raw_description in attributes.items():
        name = field_name(raw_name)
        while name in used:
            name = f"{name}_"
        used.add(name)
        namespace["__annotations__"][name] = Optional[str]
        namespace[name] = Field(default=None, description=str(raw_description or name))

    return type(class_name(type_name), (BaseModel,), namespace)
