from __future__ import annotations

from abc import ABC, abstractmethod


class AdapterError(RuntimeError):
    """AdapterError скрывает детали, которые могут раскрыть значение секрета."""


class ReplicaAdapter(ABC):
    @abstractmethod
    def probe(self, uri: str) -> bool:
        """Проверяет наличие реплики без чтения её содержимого."""

    @abstractmethod
    def read(self, uri: str) -> bytes:
        """Передаёт значение только внутреннему процессу синхронизации."""

    @abstractmethod
    def write(self, uri: str, value: bytes) -> None:
        """Атомарно заменяет реплику без передачи значения через argv."""
