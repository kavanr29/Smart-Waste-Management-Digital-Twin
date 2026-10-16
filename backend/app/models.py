from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import relationship
from .core import Base


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(dt):
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class Location(Base):
    __tablename__ = 'locations'
    id = Column(Integer, primary_key=True)
    name = Column(String, index=True)
    zone = Column(String)
    created_at = Column(DateTime, default=now)
    bins = relationship('Bin', back_populates='location')


class Bin(Base):
    __tablename__ = 'bins'
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, index=True)
    name = Column(String)
    capacity_kg = Column(Float, default=100)
    fill_kg = Column(Float, default=0)
    target_stream = Column(String, default='recyclable')
    status = Column(String, default='EMPTY')
    accumulation_rate = Column(Float, default=0)
    contamination = Column(Float, default=0)
    last_collection = Column(DateTime, default=now)
    location_id = Column(Integer, ForeignKey('locations.id'))
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)
    location = relationship('Location', back_populates='bins')
    events = relationship('WasteEvent', back_populates='bin')
    collections = relationship('Collection', back_populates='bin')


class WasteCategory(Base):
    __tablename__ = 'waste_categories'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    recyclable = Column(Boolean, default=True)


class WasteEvent(Base):
    __tablename__ = 'waste_events'
    id = Column(Integer, primary_key=True)
    object_name = Column(String)
    category = Column(String, index=True)
    confidence = Column(Float)
    weight_kg = Column(Float)
    source = Column(String, default='demo')
    image_name = Column(String, nullable=True)
    bin_id = Column(Integer, ForeignKey('bins.id'))
    # Nullable so all historic event rows remain valid after additive migration.
    idempotency_key = Column(String(128), nullable=True, unique=True)
    created_at = Column(DateTime, default=now, index=True)
    bin = relationship('Bin', back_populates='events')


class Collection(Base):
    __tablename__ = 'collections'
    id = Column(Integer, primary_key=True)
    bin_id = Column(Integer, ForeignKey('bins.id'))
    weight_kg = Column(Float)
    collected_by = Column(String, default='demo-operator')
    notes = Column(Text, nullable=True)
    status = Column(String, default='COMPLETED')
    created_at = Column(DateTime, default=now)
    bin = relationship('Bin', back_populates='collections')


class RecyclingEvent(Base):
    __tablename__ = 'recycling_events'
    id = Column(Integer, primary_key=True)
    category = Column(String)
    input_kg = Column(Float)
    recovered_kg = Column(Float)
    rejected_kg = Column(Float)
    contamination = Column(Float)
    destination = Column(String, nullable=True)
    sorting_id = Column(Integer, ForeignKey('sorting_events.id'), nullable=True)
    created_at = Column(DateTime, default=now)


class SortingEvent(Base):
    __tablename__ = 'sorting_events'
    id = Column(Integer, primary_key=True)
    collection_id = Column(Integer, ForeignKey('collections.id'), nullable=True)
    input_kg = Column(Float)
    sorted_kg = Column(Float)
    rejected_kg = Column(Float)
    category = Column(String, default='mixed')
    created_at = Column(DateTime, default=now)


class WasteFlowEvent(Base):
    __tablename__ = 'waste_flow_events'
    id = Column(Integer, primary_key=True)
    stage = Column(String, index=True)
    quantity_kg = Column(Float)
    source_event_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=now)


class Simulation(Base):
    __tablename__ = 'simulations'
    id = Column(Integer, primary_key=True)
    collection_frequency = Column(Float)
    segregation_improvement = Column(Float)
    created_at = Column(DateTime, default=now)


class AiReport(Base):
    __tablename__ = 'ai_reports'
    id = Column(Integer, primary_key=True)
    title = Column(String)
    content = Column(Text)
    mode = Column(String)
    created_at = Column(DateTime, default=now)


class Alert(Base):
    __tablename__ = 'alerts'
    id = Column(Integer, primary_key=True)
    severity = Column(String)
    title = Column(String)
    detail = Column(Text)
    resolved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=now)
