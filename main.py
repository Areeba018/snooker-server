from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Enum, Boolean, Text, DECIMAL
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session as DBSession, relationship
import datetime
import enum
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from fastapi import status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
import jwt
from datetime import timedelta

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
    time_played = "time played"

class TableStatusEnum(str, enum.Enum):
    vacant = "vacant"
    occupied = "occupied"
    reserved = "reserved"
    maintenance = "maintenance"

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
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=True)  # Allow null for guest sessions
    table_id = Column(Integer, ForeignKey('tables.id'), nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    total_minutes = Column(Integer, nullable=True)
    base_rate = Column(Float, nullable=True)
    discount = Column(Float, nullable=True)
    total_cost = Column(Float, nullable=True)
    # Guest session fields
    guest_name = Column(String(100), nullable=True)
    guest_contact = Column(String(15), nullable=True)
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
    created_at: datetime.datetime
    
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
SECRET_KEY = "your-secret-key-here-change-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="admin/login")

class Token(BaseModel):
    access_token: str
    token_type: str

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.datetime.utcnow() + expires_delta
    else:
        expire = datetime.datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return username
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

@app.post("/admin/login", response_model=Token)
def admin_login(form_data: OAuth2PasswordRequestForm = Depends()):
    if form_data.username == ADMIN_USERNAME and form_data.password == ADMIN_PASSWORD:
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": form_data.username}, expires_delta=access_token_expires
        )
        return {"access_token": access_token, "token_type": "bearer"}
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

@app.get("/admin/verify")
def verify_admin(current_user: str = Depends(verify_token)):
    return {"username": current_user, "authenticated": True}

# Tables endpoints
@app.get("/tables")
def get_tables(db: DBSession = Depends(get_db)):
    """Get all tables with their current status"""
    tables = db.query(Table).all()
    return tables

@app.get("/tables/available")
def get_available_tables(db: DBSession = Depends(get_db)):
    """Get only available (vacant) tables"""
    tables = db.query(Table).filter(Table.status == TableStatusEnum.vacant).all()
    return tables

# Session endpoints
class SessionCreate(BaseModel):
    customer_id: Optional[int] = None
    guest_name: Optional[str] = None
    guest_contact: Optional[str] = None
    table_id: int
    session_type: str  # 'member' or 'guest'

class SessionOut(BaseModel):
    id: int
    customer_id: Optional[int]
    table_id: int
    start_time: datetime.datetime
    end_time: Optional[datetime.datetime]
    total_minutes: Optional[int]
    base_rate: Optional[float]
    discount: Optional[float]
    total_cost: Optional[float]
    guest_name: Optional[str]
    guest_contact: Optional[str]
    
    model_config = ConfigDict(from_attributes=True)

@app.post("/sessions/start", response_model=SessionOut)
def start_session(session_data: SessionCreate, db: DBSession = Depends(get_db)):
    """Start a new session for member or guest"""
    # Check if table is available
    table = db.query(Table).filter(Table.id == session_data.table_id).first()
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
    
    if table.status != TableStatusEnum.vacant:
        raise HTTPException(status_code=400, detail="Table is not available")
    
    # Create session
    new_session = Session(
        customer_id=session_data.customer_id,
        table_id=session_data.table_id,
        start_time=datetime.datetime.utcnow(),
        guest_name=session_data.guest_name,
        guest_contact=session_data.guest_contact
    )
    
    # Update table status
    table.status = TableStatusEnum.occupied
    
    db.add(new_session)
    db.commit()
    db.refresh(new_session)
    
    return new_session

@app.get("/sessions/active")
def get_active_sessions(db: DBSession = Depends(get_db)):
    """Get all active sessions"""
    sessions = db.query(Session).filter(Session.end_time.is_(None)).all()
    return sessions

@app.post("/tables/create-sample")
def create_sample_tables(db: DBSession = Depends(get_db)):
    """Create sample tables if they don't exist"""
    existing_tables = db.query(Table).count()
    if existing_tables == 0:
        sample_tables = [
            Table(table_name="Table 1", status=TableStatusEnum.vacant),
            Table(table_name="Table 2", status=TableStatusEnum.vacant),
            Table(table_name="Table 3", status=TableStatusEnum.vacant),
            Table(table_name="Table 4", status=TableStatusEnum.vacant),
            Table(table_name="Table 5", status=TableStatusEnum.vacant),
            Table(table_name="Table 6", status=TableStatusEnum.vacant),
        ]
        for table in sample_tables:
            db.add(table)
        db.commit()
        return {"message": "Sample tables created successfully"}
    else:
        return {"message": f"Tables already exist ({existing_tables} tables found)"}

# Table management endpoints
class TableCreate(BaseModel):
    table_name: str
    status: str = "vacant"

class TableUpdate(BaseModel):
    table_name: Optional[str] = None
    status: Optional[str] = None

class TableOut(BaseModel):
    id: int
    table_name: str
    status: str
    
    model_config = ConfigDict(from_attributes=True)

@app.post("/tables/create", response_model=TableOut)
def create_table(table_data: TableCreate, db: DBSession = Depends(get_db)):
    """Create a new table"""
    # Check if table name already exists
    existing_table = db.query(Table).filter(Table.table_name == table_data.table_name).first()
    if existing_table:
        raise HTTPException(status_code=400, detail="Table name already exists")
    
    new_table = Table(
        table_name=table_data.table_name,
        status=TableStatusEnum(table_data.status)
    )
    db.add(new_table)
    db.commit()
    db.refresh(new_table)
    return new_table

@app.put("/tables/{table_id}", response_model=TableOut)
def update_table(table_id: int, table_data: TableUpdate, db: DBSession = Depends(get_db)):
    """Update a table"""
    table = db.query(Table).filter(Table.id == table_id).first()
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
    
    # Check if new table name already exists (if provided)
    if table_data.table_name and table_data.table_name != table.table_name:
        existing_table = db.query(Table).filter(Table.table_name == table_data.table_name).first()
        if existing_table:
            raise HTTPException(status_code=400, detail="Table name already exists")
        table.table_name = table_data.table_name
    
    if table_data.status:
        table.status = TableStatusEnum(table_data.status)
    
    db.commit()
    db.refresh(table)
    return table

@app.delete("/tables/{table_id}")
def delete_table(table_id: int, db: DBSession = Depends(get_db)):
    """Delete a table"""
    table = db.query(Table).filter(Table.id == table_id).first()
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
    
    # Check if table has active sessions (simplified query to avoid column issues)
    try:
        active_sessions = db.query(Session.id).filter(
            Session.table_id == table_id,
            Session.end_time == None
        ).count()
    except Exception:
        # If there's a column issue, assume no active sessions for now
        active_sessions = 0
    
    if active_sessions > 0:
        raise HTTPException(status_code=400, detail="Cannot delete table with active sessions")
    
    db.delete(table)
    db.commit()
    return {"message": "Table deleted successfully"} 