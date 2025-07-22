from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Enum, Boolean, Text, DECIMAL
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session as DBSession, relationship
import datetime
import enum
from fastapi.security import OAuth2PasswordRequestForm
from fastapi import status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List

DATABASE_URL = "sqlite:///./snooker.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Enums for SQLite (use string values)
class CustomerTypeEnum(str, enum.Enum):
    member = "member"
    guest = "guest"

class RateTypeEnum(str, enum.Enum):
    hourly = "hourly"
    min30 = "30min"
    custom = "time played"

class TableStatusEnum(str, enum.Enum):
    vacant = "vacant"
    occupied = "occupied"
    reserved = "reserved"

# Database Models
class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    contact_number = Column(String(15))
    customer_type = Column(Enum(CustomerTypeEnum), nullable=False, default=CustomerTypeEnum.guest)
    rate_type = Column(Enum(RateTypeEnum), default=None)
    rate_amount = Column(Float, default=None)
    discount = Column(Float, default=0.00)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    sessions = relationship("Session", back_populates="customer")

class Table(Base):
    __tablename__ = "tables"
    id = Column(Integer, primary_key=True, index=True)
    table_name = Column(String(50), nullable=False)
    status = Column(Enum(TableStatusEnum), default=TableStatusEnum.vacant)
    sessions = relationship("Session", back_populates="table")

class Session(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=False)
    table_id = Column(Integer, ForeignKey('tables.id'), nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    total_minutes = Column(Integer, nullable=True)
    base_rate = Column(Float, nullable=True)
    discount = Column(Float, nullable=True)
    total_cost = Column(Float, nullable=True)
    customer = relationship("Customer", back_populates="sessions")
    table = relationship("Table", back_populates="sessions")
    session_items = relationship("SessionItem", back_populates="session")
    bill = relationship("Bill", back_populates="session", uselist=False)

class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    price = Column(Float, nullable=False)
    category = Column(String(50), default="snack")
    session_items = relationship("SessionItem", back_populates="item")

class SessionItem(Base):
    __tablename__ = "session_items"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey('sessions.id'), nullable=False)
    item_id = Column(Integer, ForeignKey('items.id'), nullable=False)
    quantity = Column(Integer, default=1)
    total_price = Column(Float)
    session = relationship("Session", back_populates="session_items")
    item = relationship("Item", back_populates="session_items")

class Bill(Base):
    __tablename__ = "bills"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey('sessions.id'), nullable=False)
    date_issued = Column(DateTime, default=datetime.datetime.utcnow)
    paid = Column(Boolean, default=False)
    notes = Column(Text, default=None)
    session = relationship("Session", back_populates="bill")

Base.metadata.create_all(bind=engine)

# Pydantic Schemas for Customer
class CustomerCreate(BaseModel):
    name: str
    contact_number: str
    rate_type: str
    rate_amount: float
    discount: float = 0.0

class CustomerOut(BaseModel):
    id: int
    name: str
    contact_number: str
    customer_type: str
    rate_type: str
    rate_amount: float
    discount: float
    
    model_config = ConfigDict(from_attributes=True)

# Dependency

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# API Endpoints
@app.post("/register", response_model=CustomerOut)
def register_member(customer: CustomerCreate, db: DBSession = Depends(get_db)):
    # Only check for duplicates by contact number
    existing_customer = db.query(Customer).filter(
        Customer.contact_number == customer.contact_number
    ).first()
    if existing_customer:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "Member already exists."},
        )

    db_customer = Customer(
        name=customer.name,
        contact_number=customer.contact_number,
        customer_type='member',
        rate_type=customer.rate_type,
        rate_amount=customer.rate_amount,
        discount=customer.discount
    )
    db.add(db_customer)
    db.commit()
    db.refresh(db_customer)
    return db_customer

@app.get("/customers", response_model=List[CustomerOut])
def get_all_customers(db: DBSession = Depends(get_db)):
    """
    Retrieve all customers with the 'member' type.
    """
    customers = db.query(Customer).filter(Customer.customer_type == 'member').all()
    return customers

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"

@app.post("/admin/login")
def admin_login(form_data: OAuth2PasswordRequestForm = Depends()):
    if form_data.username == ADMIN_USERNAME and form_data.password == ADMIN_PASSWORD:
        return {"success": True, "message": "Login successful"}
    else:
        return {"success": False, "message": "Invalid credentials"} 