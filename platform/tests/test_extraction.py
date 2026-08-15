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


# ---------------------------------------------------------------------------
# Import resolver — relative import resolution
# ---------------------------------------------------------------------------

from src.extraction.models import Entity
from src.resolution.import_resolver import (
    _resolve_relative,
    _find_python_module_by_path,
)


def _make_module_entity(entity_id: str, file_path: str) -> Entity:
    return Entity(
        id=entity_id,
        type="module",
        name=file_path.split("/")[-1],
        file_path=file_path,
        start_line=1,
        end_line=1,
        parent_id=None,
        language="python",
        has_docstring=False,
        source="",
    )


class TestFindPythonModuleByPath:
    """_find_python_module_by_path: path → entity ID lookup."""

    def _make_registry(self, *file_paths):
        entities = {fp: _make_module_entity("py." + fp[:-3].replace("/", "."), fp) for fp in file_paths}
        return entities, {fp: ent for fp, ent in entities.items()}

    def test_plain_file(self):
        module_entities, file_to_module = self._make_registry("models.py")
        result = _find_python_module_by_path("models", file_to_module, module_entities)
        assert result == "py.models"

    def test_nested_file(self):
        module_entities, file_to_module = self._make_registry("env/env.py")
        result = _find_python_module_by_path("env/env", file_to_module, module_entities)
        assert result == "py.env.env"

    def test_package_init(self):
        module_entities, file_to_module = self._make_registry("env/__init__.py")
        result = _find_python_module_by_path("env", file_to_module, module_entities)
        assert result == "py.env.__init__"

    def test_leading_dotslash_stripped(self):
        """'./models' must resolve identically to 'models'."""
        module_entities, file_to_module = self._make_registry("models.py")
        result = _find_python_module_by_path("./models", file_to_module, module_entities)
        assert result == "py.models"

    def test_empty_path_returns_none(self):
        result = _find_python_module_by_path("", {}, {})
        assert result is None

    def test_dot_only_returns_none(self):
        result = _find_python_module_by_path(".", {}, {})
        assert result is None

    def test_unknown_path_returns_none(self):
        module_entities, file_to_module = self._make_registry("models.py")
        result = _find_python_module_by_path("nonexistent", file_to_module, module_entities)
        assert result is None


class TestResolveRelativePython:
    """_resolve_relative: flat-root repo layout with cross-directory relative imports."""

    def _registries(self, *file_paths):
        """Build module_entities and file_path_to_module from a list of file paths."""
        module_entities = {}
        file_to_module = {}
        for fp in file_paths:
            stem = fp[:-3].replace("/", ".")  # strip .py, / → .
            eid = "py." + stem
            ent = _make_module_entity(eid, fp)
            module_entities[eid] = ent
            file_to_module[fp] = ent
        return module_entities, file_to_module

    # ── Single-dot (same package) ──────────────────────────────────────────

    def test_single_dot_sibling(self):
        """from .tools import X  inside  env/reward.py → env/tools.py"""
        module_entities, file_to_module = self._registries("env/reward.py", "env/tools.py")
        result = _resolve_relative(".tools", "env/reward.py", "python", module_entities, file_to_module)
        assert result == "py.env.tools"

    def test_single_dot_package_itself(self):
        """from . import something  inside  env/reward.py → env/__init__.py"""
        module_entities, file_to_module = self._registries("env/reward.py", "env/__init__.py")
        # module_path has no rest component
        result = _resolve_relative(".", "env/reward.py", "python", module_entities, file_to_module)
        assert result == "py.env.__init__"

    # ── Double-dot (parent package) — the broadbandcare-grpo case ──────────

    def test_double_dot_to_root_file(self):
        """from ..models import X  inside  server/app.py → models.py at repo root"""
        module_entities, file_to_module = self._registries(
            "server/app.py", "models.py", "env/env.py"
        )
        result = _resolve_relative("..models", "server/app.py", "python", module_entities, file_to_module)
        assert result == "py.models"

    def test_double_dot_to_subpackage(self):
        """from ..env.env import X  inside  server/app.py → env/env.py"""
        module_entities, file_to_module = self._registries(
            "server/app.py", "env/env.py", "env/__init__.py"
        )
        result = _resolve_relative("..env.env", "server/app.py", "python", module_entities, file_to_module)
        assert result == "py.env.env"

    def test_double_dot_to_package_init(self):
        """from ..env import X  inside  server/app.py → env/__init__.py"""
        module_entities, file_to_module = self._registries(
            "server/app.py", "env/__init__.py"
        )
        result = _resolve_relative("..env", "server/app.py", "python", module_entities, file_to_module)
        assert result == "py.env.__init__"

    # ── Standard python/ subdirectory layout (must still work) ────────────

    def test_standard_python_subdir_single_dot(self):
        """from .base import X  inside  python/models/user.py → python/models/base.py"""
        module_entities, file_to_module = self._registries(
            "python/models/user.py", "python/models/base.py"
        )
        result = _resolve_relative(".base", "python/models/user.py", "python", module_entities, file_to_module)
        assert result == "py.python.models.base"

    def test_standard_python_subdir_double_dot(self):
        """from ..services import X  inside  python/models/user.py → python/services.py"""
        module_entities, file_to_module = self._registries(
            "python/models/user.py", "python/services.py"
        )
        result = _resolve_relative("..services", "python/models/user.py", "python", module_entities, file_to_module)
        assert result == "py.python.services"

    # ── Edge cases ─────────────────────────────────────────────────────────

    def test_unresolvable_returns_none(self):
        module_entities, file_to_module = self._registries("server/app.py")
        result = _resolve_relative("..nonexistent", "server/app.py", "python", module_entities, file_to_module)
        assert result is None

    def test_triple_dot_from_nested(self):
        """from ...utils import X  inside  a/b/c.py → utils.py at root"""
        module_entities, file_to_module = self._registries("a/b/c.py", "utils.py")
        result = _resolve_relative("...utils", "a/b/c.py", "python", module_entities, file_to_module)
        assert result == "py.utils"


# ---------------------------------------------------------------------------
# _walk_flat — try/except import visibility
# ---------------------------------------------------------------------------

import textwrap
import tree_sitter_python
import tree_sitter as _ts
from src.languages.python_adapter import PythonAdapter


def _parse(source: str) -> _ts.Node:
    lang = tree_sitter_python.language()
    parser = _ts.Parser(_ts.Language(lang))
    return parser.parse(source.encode()).root_node


class TestWalkFlatTryExcept:
    """_walk_flat must yield import nodes inside try/except blocks."""

    def _import_module_paths(self, source: str) -> list[str]:
        """Return all module_path strings found by get_imports() for *source*."""
        adapter = PythonAdapter()
        root = _parse(source)
        stmts = adapter.get_imports(root, source.encode(), "server/app.py")
        return [s.module_path for s in stmts]

    def test_try_body_relative_import_found(self):
        """from ..models import X inside try: must be seen."""
        src = textwrap.dedent("""\
            try:
                from ..models import Foo
            except ImportError:
                pass
        """)
        paths = self._import_module_paths(src)
        assert "..models" in paths

    def test_except_body_absolute_fallback_found(self):
        """Absolute fallback import inside except: must also be seen."""
        src = textwrap.dedent("""\
            try:
                from ..models import Foo
            except ImportError:
                from models import Foo
        """)
        paths = self._import_module_paths(src)
        assert "..models" in paths
        assert "models" in paths

    def test_both_try_and_except_imports_found(self):
        """The exact broadbandcare-grpo server/app.py pattern."""
        src = textwrap.dedent("""\
            try:
                from ..models import BroadbandcareAction, BroadbandcareObservation
                from ..env.env import BroadbandSupportEnv
            except ImportError:
                from models import BroadbandcareAction, BroadbandcareObservation
                from env.env import BroadbandSupportEnv
        """)
        paths = self._import_module_paths(src)
        assert "..models" in paths
        assert "..env.env" in paths
        assert "models" in paths
        assert "env.env" in paths

    def test_finally_clause_imports_found(self):
        """Imports inside finally: are also yielded."""
        src = textwrap.dedent("""\
            try:
                pass
            finally:
                from cleanup import teardown
        """)
        paths = self._import_module_paths(src)
        assert "cleanup" in paths

    def test_normal_top_level_imports_unaffected(self):
        """Regular top-level imports still work after the change."""
        src = textwrap.dedent("""\
            import os
            from pathlib import Path
            try:
                from ..utils import helper
            except ImportError:
                from utils import helper
        """)
        paths = self._import_module_paths(src)
        assert "os" in paths
        assert "pathlib" in paths
        assert "..utils" in paths
        assert "utils" in paths

    def test_imports_inside_function_not_leaked(self):
        """Imports inside a function body must NOT be yielded by _walk_flat."""
        src = textwrap.dedent("""\
            def setup():
                from hidden import secret
        """)
        paths = self._import_module_paths(src)
        assert "hidden" not in paths


class TestWalkFlatTryExceptEndToEnd:
    """End-to-end: entity extraction + import resolution for try/except layout."""

    def test_try_except_imports_produce_relationships(self, tmp_path):
        """
        server/app.py with try/except relative imports should produce IMPORTS
        edges to models.py and env/env.py after resolution.
        """
        # Build a minimal flat-root repo layout in tmp_path
        server_dir = tmp_path / "server"
        server_dir.mkdir()
        env_dir = tmp_path / "env"
        env_dir.mkdir()

        (server_dir / "__init__.py").write_text("", encoding="utf-8")
        (env_dir / "__init__.py").write_text("", encoding="utf-8")

        (tmp_path / "models.py").write_text(
            "class BroadbandcareAction:\n    pass\n"
            "class BroadbandcareObservation:\n    pass\n",
            encoding="utf-8",
        )
        (env_dir / "env.py").write_text(
            "class BroadbandSupportEnv:\n    pass\n",
            encoding="utf-8",
        )
        (server_dir / "app.py").write_text(
            textwrap.dedent("""\
                try:
                    from ..models import BroadbandcareAction, BroadbandcareObservation
                    from ..env.env import BroadbandSupportEnv
                except ImportError:
                    from models import BroadbandcareAction, BroadbandcareObservation
                    from env.env import BroadbandSupportEnv
            """),
            encoding="utf-8",
        )

        from src.extraction.entity_extractor import EntityExtractor
        from src.resolution import resolve_relationships
        from src.languages import ADAPTER_REGISTRY

        extractor = EntityExtractor()
        entities, contains_rels = extractor.extract_repository(str(tmp_path))
        rels = resolve_relationships(entities, str(tmp_path), ADAPTER_REGISTRY)

        # Collect all IMPORTS edge pairs
        imports = {
            (r.source_id, r.target_id)
            for r in rels
            if r.type == "IMPORTS" and r.target_id
        }

        # server/app.py module entity
        server_app_id = next(
            (e.id for e in entities if e.file_path == "server/app.py" and e.type == "module"),
            None,
        )
        assert server_app_id is not None, "server/app.py module entity not found"

        # models.py should be reachable
        models_ids = {e.id for e in entities if "models" in e.file_path}
        env_ids = {e.id for e in entities if "env" in e.file_path and e.file_path != "env/__init__.py"}

        # At least one IMPORTS edge from server/app.py into models or env
        from_server = {tgt for (src, tgt) in imports if src == server_app_id}
        assert from_server & models_ids, (
            f"No IMPORTS edge from server/app.py to models.py. "
            f"from_server={from_server}, models_ids={models_ids}"
        )
        assert from_server & env_ids, (
            f"No IMPORTS edge from server/app.py to env/env.py. "
            f"from_server={from_server}, env_ids={env_ids}"
        )
