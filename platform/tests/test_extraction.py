import sys
from pathlib import Path

PLATFORM_DIR = Path(__file__).resolve().parent.parent
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

import pytest
from src.extraction.entity_extractor import EntityExtractor
from src.extraction.models import Entity, Relationship


def test_entity_extractor_initialization():
    extractor = EntityExtractor()
    assert extractor.parser is not None


def test_generate_module_id():
    extractor = EntityExtractor()
    assert extractor.generate_module_id("python/models/base.py", "python") == "py.models.base"
    assert extractor.generate_module_id("typescript/models/user.model.ts", "typescript") == "ts.models.user_model"
    assert extractor.generate_module_id("docs/ARCHITECTURE.md", "markdown") == "docs.architecture"


def test_python_snippet_extraction(tmp_path):
    py_file = tmp_path / "test_sample.py"
    py_file.write_text(
        '"""Module docstring."""\n\n'
        "class TestClass:\n"
        '    """Class docstring."""\n'
        "    def test_method(self):\n"
        '        """Method docstring."""\n'
        "        pass\n",
        encoding="utf-8",
    )

    extractor = EntityExtractor()
    entities, rels = extractor.extract_file(str(py_file), "python/test_sample.py", "python")

    assert len(entities) == 3
    mod, cls, mth = entities[0], entities[1], entities[2]

    assert mod.type == "module"
    assert mod.id == "py.test_sample"
    assert mod.has_docstring is True

    assert cls.type == "class"
    assert cls.name == "TestClass"
    assert cls.parent_id == "py.test_sample"
    assert cls.has_docstring is True

    assert mth.type == "method"
    assert mth.name == "test_method"
    assert mth.parent_id == "py.test_sample.TestClass"
    assert mth.has_docstring is True

    contains_rels = [r for r in rels if r.type == "CONTAINS"]
    assert len(contains_rels) == 2
