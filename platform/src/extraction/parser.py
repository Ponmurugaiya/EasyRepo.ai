from typing import Optional, Dict
import tree_sitter
import tree_sitter_python
import tree_sitter_typescript


class TreeSitterParser:
    """Manages tree-sitter parsers and languages for supported codebases."""

    def __init__(self) -> None:
        self._languages: Dict[str, tree_sitter.Language] = {}
        self._parsers: Dict[str, tree_sitter.Parser] = {}
        self._init_languages()

    def _init_languages(self) -> None:
        py_lang = tree_sitter.Language(tree_sitter_python.language())
        ts_lang = tree_sitter.Language(tree_sitter_typescript.language_typescript())

        self._languages["python"] = py_lang
        self._languages["typescript"] = ts_lang

        self._parsers["python"] = tree_sitter.Parser(py_lang)
        self._parsers["typescript"] = tree_sitter.Parser(ts_lang)

    def get_parser(self, language: str) -> Optional[tree_sitter.Parser]:
        return self._parsers.get(language)

    def parse(self, content: bytes, language: str) -> Optional[tree_sitter.Tree]:
        parser = self.get_parser(language)
        if parser is None:
            return None
        return parser.parse(content)
