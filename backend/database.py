"""
Database models for ZERO Terminal
"""
from sqlalchemy import create_engine, Column, String, Float, Integer, Boolean, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

# Database configuration
DATABASE_URL = "sqlite:///./zero_terminal.db"

# Initialize engine and session
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False}, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Database models
class User(Base):
    """User model for authentication."""
    __tablename__ = "users"
    
    id = Column(String, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    role = Column(String, default="viewer")  # viewer, trader, admin
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, onupdate=datetime.utcnow)


class PredictionCache(Base):
    """Cache for prediction results."""
    __tablename__ = "prediction_cache"
    
    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, index=True)
    index_name = Column(String, index=True)
    data = Column(Text, nullable=False)  # JSON stored as text
    created_at = Column(DateTime, default=datetime.utcnow)


class LivePriceCache(Base):
    """Cache for live price data."""
    __tablename__ = "live_price_cache"
    
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True)
    data = Column(Text, nullable=False)  # JSON stored as text
    timestamp = Column(DateTime, default=datetime.utcnow)


class NewsCache(Base):
    """Cache for news data."""
    __tablename__ = "news_cache"
    
    id = Column(Integer, primary_key=True, index=True)
    cache_key = Column(String, unique=True, index=True)
    data = Column(Text, nullable=False)  # JSON stored as text
    timestamp = Column(DateTime, default=datetime.utcnow)


class Order(Base):
    """Order model for trading."""
    __tablename__ = "orders"
    
    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, index=True, nullable=False)
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)  # BUY, SELL
    order_type = Column(String, nullable=False)  # MARKET, LIMIT, SL
    quantity = Column(Integer, nullable=False)
    limit_price = Column(Float)
    status = Column(String, default="PENDING")  # PENDING, FILLED, REJECTED
    created_at = Column(DateTime, default=datetime.utcnow)
    filled_at = Column(DateTime)


class Strategy(Base):
    """Strategy model for trading strategies."""
    __tablename__ = "strategies"
    
    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, index=True, nullable=False)
    name = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    parameters = Column(Text, nullable=False)  # JSON stored as text
    backtest_results = Column(Text)  # JSON stored as text
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)


# Initialize database tables
def init_db():
    """Create all tables."""
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully")
