"""Non-destructive regression checks for Alembic environment transaction ordering."""

import ast
import unittest
from pathlib import Path


class AlembicEnvironmentTest(unittest.TestCase):
    def test_search_path_is_committed_before_alembic_transaction_setup(self) -> None:
        source_path = Path(__file__).resolve().parents[1] / "alembic" / "env.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        helper = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_set_committed_search_path"
        )
        calls = [
            node.func.attr
            for node in ast.walk(helper)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "connection"
        ]
        self.assertLess(calls.index("execute"), calls.index("commit"))


if __name__ == "__main__":
    unittest.main()
