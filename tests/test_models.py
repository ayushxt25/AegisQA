"""ORM mapper regression coverage."""

import unittest

from sqlalchemy.orm import configure_mappers

from backend.models import Base, ExecutionEvent


class ModelConfigurationTest(unittest.TestCase):
    def test_all_mappers_configure_and_event_metadata_keeps_database_column(self) -> None:
        configure_mappers()
        self.assertIn("metadata", ExecutionEvent.__table__.c)
        self.assertIn("event_metadata", ExecutionEvent.__mapper__.attrs)
        self.assertIs(Base.metadata, ExecutionEvent.metadata)


if __name__ == "__main__":
    unittest.main()
