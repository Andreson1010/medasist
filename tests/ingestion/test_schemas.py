from __future__ import annotations

import pytest

from medasist.config import Settings
from medasist.ingestion import schemas
from medasist.ingestion.schemas import DocType, collection_name


@pytest.fixture
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


class TestCollectionName:
    def test_default_names_per_doc_type(self, settings: Settings):
        expected = {
            DocType.BULA: "bulas",
            DocType.DIRETRIZ: "diretrizes",
            DocType.PROTOCOLO: "protocolos",
            DocType.MANUAL: "manuais",
        }
        for doc_type, name in expected.items():
            assert collection_name(doc_type, settings) == name

    def test_reads_custom_names_from_settings(self):
        custom = Settings(collection_bulas="bulas_v2")  # type: ignore[call-arg]
        assert collection_name(DocType.BULA, custom) == "bulas_v2"
        assert collection_name(DocType.MANUAL, custom) == "manuais"

    def test_all_doc_types_are_mapped(self, settings: Settings):
        for doc_type in DocType:
            name = collection_name(doc_type, settings)
            assert isinstance(name, str)
            assert len(name) > 0

    def test_unmapped_doc_type_raises_value_error(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(schemas, "_COLLECTION_ACCESSORS", {})
        with pytest.raises(ValueError, match="sem coleção mapeada"):
            collection_name(DocType.BULA, settings)
