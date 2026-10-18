"""
Unit tests for MinionQA Database Module.
"""

from sqlalchemy import Column, Integer, String
from minion_qa.database import Base, engine, get_db, init_db, SessionLocal


class DummyModel(Base):
    __tablename__ = "dummy_test_table"
    id = Column(Integer, primary_key=True)
    name = Column(String(50))


def test_database_initialization():
    """Verify that init_db creates tables and sessions can read/write."""
    init_db()
    
    db = next(get_db())
    try:
        dummy = DummyModel(name="test_record")
        db.add(dummy)
        db.commit()
        db.refresh(dummy)
        assert dummy.id is not None
        assert dummy.name == "test_record"

        found = db.query(DummyModel).filter(DummyModel.id == dummy.id).first()
        assert found is not None
        assert found.name == "test_record"

        db.delete(found)
        db.commit()
    finally:
        db.close()
