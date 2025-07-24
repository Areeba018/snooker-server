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
    partially_vacant = "partially_vacant"
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
    rate_type = Column(String(50), nullable=True)  # Store rate type for guest sessions
    # Game session fields
    game_type = Column(String(50), default="snooker")
    total_players = Column(Integer, default=1)
    current_players = Column(Integer, default=1)
    all_players_data = Column(Text, nullable=True)  # Store all players as JSON
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
    """Get available (vacant and partially vacant) tables"""
    tables = db.query(Table).filter(
        Table.status.in_([TableStatusEnum.vacant, TableStatusEnum.partially_vacant])
    ).all()
    return tables

# Session endpoints
class SessionCreate(BaseModel):
    customer_id: Optional[int] = None
    guest_name: Optional[str] = None
    guest_contact: Optional[str] = None
    table_id: int
    session_type: str  # 'member' or 'guest'
    rate_type: Optional[str] = None
    rate_amount: Optional[float] = None
    game_type: Optional[str] = "snooker"  # snooker, pool, etc.
    total_players: Optional[int] = 1
    current_players: Optional[int] = 1
    all_players: Optional[list] = None

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
    
    # Check for existing active session for the same customer
    if session_data.customer_id:
        # For member sessions - check by customer_id
        existing_session = db.query(Session).filter(
            Session.customer_id == session_data.customer_id,
            Session.end_time.is_(None)
        ).first()
        if existing_session:
            raise HTTPException(
                status_code=409, 
                detail=f"Customer already has an active session on Table {existing_session.table_id}"
            )
    elif session_data.guest_name and session_data.guest_contact:
        # For guest sessions - check by guest name and contact
        existing_session = db.query(Session).filter(
            Session.guest_name == session_data.guest_name,
            Session.guest_contact == session_data.guest_contact,
            Session.end_time.is_(None)
        ).first()
        if existing_session:
            raise HTTPException(
                status_code=409, 
                detail=f"Guest '{session_data.guest_name}' already has an active session on Table {existing_session.table_id}"
            )
    
    # Create session with correct time (Pakistani time UTC+5)
    pakistani_time = datetime.datetime.utcnow() + datetime.timedelta(hours=5)
    
    # Store all players data as JSON
    import json
    all_players_json = json.dumps(session_data.all_players) if session_data.all_players else None
    
    # For member sessions, get the rate amount and type from customer record
    base_rate = session_data.rate_amount
    rate_type = session_data.rate_type
    if session_data.customer_id:
        customer = db.query(Customer).filter(Customer.id == session_data.customer_id).first()
        if customer:
            base_rate = customer.rate_amount
            rate_type = customer.rate_type
    
    new_session = Session(
        customer_id=session_data.customer_id,
        table_id=session_data.table_id,
        start_time=pakistani_time,
        guest_name=session_data.guest_name,
        guest_contact=session_data.guest_contact,
        base_rate=base_rate,
        rate_type=rate_type,
        game_type=session_data.game_type,
        total_players=session_data.total_players,
        current_players=session_data.current_players,
        all_players_data=all_players_json
    )
    
    # Update table status based on player count
    current_players = getattr(session_data, 'current_players', 1)
    total_players = getattr(session_data, 'total_players', 1)
    
    print(f"Debug: current_players={current_players}, total_players={total_players}")
    
    if current_players < total_players:
        table.status = TableStatusEnum.partially_vacant
        print(f"Debug: Setting table status to partially_vacant")
    else:
        table.status = TableStatusEnum.occupied
        print(f"Debug: Setting table status to occupied")
    
    db.add(new_session)
    db.commit()
    db.refresh(new_session)
    
    return new_session

@app.get("/sessions/active")
def get_active_sessions(db: DBSession = Depends(get_db)):
    """Get all active sessions with customer details"""
    sessions = db.query(Session).filter(Session.end_time.is_(None)).all()
    
    # Enrich sessions with customer data
    enriched_sessions = []
    for session in sessions:
        session_dict = {
            "id": session.id,
            "table_id": session.table_id,
            "customer_id": session.customer_id,
            "guest_name": session.guest_name,
            "guest_contact": session.guest_contact,
            "start_time": session.start_time,
            "end_time": session.end_time,
            "total_minutes": session.total_minutes,
            "base_rate": session.base_rate,
            "discount": session.discount,
            "total_cost": session.total_cost,
            "customer_name": None,
            "customer_contact": None,
            "rate_type": None,
            "rate_amount": session.base_rate,
            "table_name": None,
            "game_type": getattr(session, 'game_type', 'snooker'),
            "total_players": getattr(session, 'total_players', 1),
            "current_players": getattr(session, 'current_players', 1),
            "all_players": []
        }
        
        # Parse all players data if available
        if hasattr(session, 'all_players_data') and session.all_players_data:
            try:
                import json
                session_dict["all_players"] = json.loads(session.all_players_data)
            except:
                session_dict["all_players"] = []
        
        # Get table name
        table = db.query(Table).filter(Table.id == session.table_id).first()
        if table:
            session_dict["table_name"] = table.table_name
        
        # If it's a member session, get customer details
        if session.customer_id:
            customer = db.query(Customer).filter(Customer.id == session.customer_id).first()
            if customer:
                session_dict["customer_name"] = customer.name
                session_dict["customer_contact"] = customer.contact_number
                session_dict["rate_type"] = customer.rate_type
        else:
            # For guest sessions, use the rate type stored in the session
            if session.rate_type:
                session_dict["rate_type"] = session.rate_type
            else:
                session_dict["rate_type"] = "Guest"
        
        enriched_sessions.append(session_dict)
    
    return enriched_sessions

class AddPlayerRequest(BaseModel):
    player: dict

class EndPlayerRequest(BaseModel):
    player_index: int

@app.post("/sessions/{session_id}/end-player")
def end_player_session(session_id: int, player_data: EndPlayerRequest, db: DBSession = Depends(get_db)):
    """End a specific player's session"""
    try:
        print(f"DEBUG: End player session called for session_id={session_id}, player_index={player_data.player_index}")
        
        session = db.query(Session).filter(Session.id == session_id).first()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        if session.end_time:
            raise HTTPException(status_code=400, detail="Session already ended")
        
        # Get current players data
        import json
        current_players_data = []
        if hasattr(session, 'all_players_data') and session.all_players_data:
            try:
                current_players_data = json.loads(session.all_players_data)
                print(f"DEBUG: Parsed players data: {current_players_data}")
            except Exception as e:
                print(f"DEBUG: Error parsing players data: {e}")
                current_players_data = []
        else:
            print(f"DEBUG: No all_players_data found for session {session_id}")
        
        print(f"DEBUG: Current players count: {len(current_players_data)}, Requested index: {player_data.player_index}")
        
        if player_data.player_index >= len(current_players_data):
            raise HTTPException(status_code=400, detail=f"Player index {player_data.player_index} out of range. Total players: {len(current_players_data)}")
        
        # Remove the specific player
        removed_player = current_players_data.pop(player_data.player_index)
        
        # Calculate cost for the removed player
        pakistani_end_time = datetime.datetime.utcnow() + datetime.timedelta(hours=5)
        duration = pakistani_end_time - session.start_time
        total_minutes = int(duration.total_seconds() / 60)
        
        # Calculate cost for the removed player
        player_cost = 0
        discount_amount = 0
        rate_type = removed_player.get('rateType', 'hourly')
        rate_amount = removed_player.get('rateAmount', session.base_rate or 0)
        
        # Convert rate_amount to float if it's a string
        if isinstance(rate_amount, str):
            try:
                rate_amount = float(rate_amount)
            except ValueError:
                rate_amount = 0.0
        
        player_type = removed_player.get('playerType', 'guest')
        
        # Calculate base cost based on rate type
        if rate_type == "hourly":
            hours = max(1, total_minutes / 60)  # Minimum 1 hour
            player_cost = hours * rate_amount
        elif rate_type == "30min":
            blocks = max(1, total_minutes / 30)  # Minimum 1 block
            player_cost = blocks * rate_amount
        elif rate_type == "time played" or rate_type == "time_played":
            # For time played, rate_amount should be per minute
            player_cost = total_minutes * rate_amount
        
        # Apply discount only for members
        if player_type == 'member' and removed_player.get('customerId'):
            customer = db.query(Customer).filter(Customer.id == removed_player.get('customerId')).first()
            if customer and customer.discount > 0:
                discount_amount = (player_cost * customer.discount) / 100
                player_cost = player_cost - discount_amount
        
        # Set customer_id if it's a member
        customer_id = None
        guest_name = removed_player.get('name')
        guest_contact = removed_player.get('contact')
        
        if removed_player.get('playerType') == 'member' and removed_player.get('customerId'):
            customer_id = removed_player.get('customerId')
            # For members, don't set guest_name and guest_contact
            guest_name = None
            guest_contact = None
            print(f"DEBUG: Setting customer_id={customer_id} for member session")
        else:
            print(f"DEBUG: Creating guest session for {removed_player.get('name')}")
        
        # Create a new session for the removed player to generate bill
        removed_player_session = Session(
            customer_id=customer_id,  # Set customer_id directly
            table_id=session.table_id,
            start_time=session.start_time,
            end_time=pakistani_end_time,
            total_minutes=total_minutes,
            base_rate=rate_amount,
            total_cost=player_cost,
            discount=discount_amount if player_type == 'member' else 0,
            guest_name=guest_name,  # Only set for guests
            guest_contact=guest_contact,  # Only set for guests
            rate_type=rate_type,
            game_type=session.game_type
        )
        
        db.add(removed_player_session)
        db.flush()  # Flush to get the session ID
        
        # Create bill for the removed player
        new_bill = Bill(
            session_id=removed_player_session.id,
            date_issued=pakistani_end_time,
            paid=False,
            notes=f"Individual player session ended - {removed_player.get('name', 'Unknown')}"
        )
        db.add(new_bill)
        
        # Update session
        session.all_players_data = json.dumps(current_players_data)
        session.current_players = len(current_players_data)  # Update to actual count
        
        # Update table status
        table = db.query(Table).filter(Table.id == session.table_id).first()
        session_ended = False
        
        if table:
            if len(current_players_data) == 0:
                # No players left, end the entire session
                session.end_time = pakistani_end_time
                table.status = TableStatusEnum.vacant
                session_ended = True
            elif len(current_players_data) == 1:
                # Only one player left, keep session active but mark as partially vacant
                table.status = TableStatusEnum.partially_vacant
            else:
                # Multiple players still active
                table.status = TableStatusEnum.partially_vacant
        
        db.commit()
        
        print(f"DEBUG: Successfully ended player session. Session ended: {session_ended}, Players remaining: {len(current_players_data)}")
        
        return {
            "message": f"Player {removed_player.get('name', 'Unknown')} session ended successfully",
            "session_id": session.id,
            "current_players": session.current_players,
            "total_players": session.total_players,
            "session_ended": session_ended,
            "players_remaining": len(current_players_data),
            "player_cost": player_cost,
            "bill_created": True
        }
    except Exception as e:
        print(f"DEBUG: Error in end_player_session: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.post("/sessions/{session_id}/add-player")
def add_player_to_session(session_id: int, player_data: AddPlayerRequest, db: DBSession = Depends(get_db)):
    """Add a new player to an existing session"""
    session = db.query(Session).filter(Session.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.end_time:
        raise HTTPException(status_code=400, detail="Cannot add player to ended session")
    
    # Check if session is full
    current_players = getattr(session, 'current_players', 1)
    total_players = getattr(session, 'total_players', 1)
    
    if current_players >= total_players:
        raise HTTPException(status_code=400, detail="Session is already full")
    
    # Get current players data
    import json
    current_players_data = []
    if hasattr(session, 'all_players_data') and session.all_players_data:
        try:
            current_players_data = json.loads(session.all_players_data)
        except:
            current_players_data = []
    
    # Add new player
    new_player = player_data.player
    current_players_data.append(new_player)
    
    # Update session
    session.all_players_data = json.dumps(current_players_data)
    session.current_players = current_players + 1
    
    # Update table status
    table = db.query(Table).filter(Table.id == session.table_id).first()
    if table and session.current_players >= session.total_players:
        table.status = TableStatusEnum.occupied
    elif table and session.current_players < session.total_players:
        table.status = TableStatusEnum.partially_vacant
    
    db.commit()
    
    return {
        "message": "Player added successfully",
        "session_id": session.id,
        "current_players": session.current_players,
        "total_players": session.total_players
    }

@app.post("/sessions/{session_id}/end")
def end_session(session_id: int, db: DBSession = Depends(get_db)):
    """End a session and calculate total cost"""
    session = db.query(Session).filter(Session.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.end_time:
        raise HTTPException(status_code=400, detail="Session already ended")
    
    # Calculate session duration and cost using current time (Pakistani time)
    pakistani_end_time = datetime.datetime.utcnow() + datetime.timedelta(hours=5)
    duration = pakistani_end_time - session.start_time
    total_minutes = int(duration.total_seconds() / 60)
    
    # Calculate cost based on rate type
    total_cost = 0
    discount_amount = 0
    
    if session.base_rate:
        if session.customer_id:
            # Member session - get rate type from customer
            customer = db.query(Customer).filter(Customer.id == session.customer_id).first()
            if customer:
                rate_type = customer.rate_type
                rate_amount = customer.rate_amount
                
                # Convert rate_amount to float if it's a string
                if isinstance(rate_amount, str):
                    try:
                        rate_amount = float(rate_amount)
                    except ValueError:
                        rate_amount = 0.0
                
                # Calculate base cost
                if rate_type == "hourly":
                    # For hourly: calculate exact hours (no minimum)
                    hours = total_minutes / 60
                    total_cost = hours * rate_amount
                elif rate_type == "30min":
                    # For 30min: calculate exact blocks (no minimum)
                    blocks = total_minutes / 30
                    total_cost = blocks * rate_amount
                elif rate_type == "time played" or rate_type == "time_played":
                    # For time played: rate_amount is per minute
                    total_cost = total_minutes * rate_amount
                
                print(f"DEBUG: Customer {customer.name}, Rate Type: {rate_type}, Rate Amount: {rate_amount}")
                print(f"DEBUG: Duration: {total_minutes} minutes, Base Cost: {total_cost}")
                
                # Apply discount for members
                if customer.discount > 0:
                    discount_amount = (total_cost * customer.discount) / 100
                    total_cost = total_cost - discount_amount
                    session.discount = discount_amount
                    print(f"DEBUG: Discount: {customer.discount}%, Discount Amount: {discount_amount}, Final Cost: {total_cost}")
                else:
                    print(f"DEBUG: No discount applied, Final Cost: {total_cost}")
                
                # Store the base cost before discount for reference
                session.base_rate = rate_amount
        else:
            # Guest session - use session rate type and amount (no discount)
            if session.rate_type:
                rate_type = session.rate_type
                rate_amount = session.base_rate
                
                if rate_type == "hourly":
                    # For hourly: calculate exact hours (no minimum)
                    hours = total_minutes / 60
                    total_cost = hours * rate_amount
                elif rate_type == "30min":
                    # For 30min: calculate exact blocks (no minimum)
                    blocks = total_minutes / 30
                    total_cost = blocks * rate_amount
                elif rate_type == "time played" or rate_type == "time_played":
                    # For time played: rate_amount is per minute
                    total_cost = total_minutes * rate_amount
            else:
                # Default to hourly for guests without rate type
                hours = max(1, total_minutes / 60)
                total_cost = hours * session.base_rate
    
    # Update session with end time and calculated values
    session.end_time = pakistani_end_time
    session.total_minutes = total_minutes
    session.total_cost = total_cost
    
    # Create bill for the session
    new_bill = Bill(
        session_id=session.id,
        date_issued=pakistani_end_time,
        paid=False,
        notes="Session ended normally"
    )
    db.add(new_bill)
    
    # Update table status to vacant
    table = db.query(Table).filter(Table.id == session.table_id).first()
    if table:
        table.status = TableStatusEnum.vacant
    
    db.commit()
    
    return {
        "message": "Session ended successfully",
        "session_id": session.id,
        "total_minutes": total_minutes,
        "total_cost": total_cost,
        "end_time": pakistani_end_time,
        "duration_formatted": f"{total_minutes // 60}h {total_minutes % 60}m" if total_minutes >= 60 else f"{total_minutes}m",
        "bill_created": True
    }

@app.post("/sessions/{session_id}/end-complete")
def end_complete_session(session_id: int, db: DBSession = Depends(get_db)):
    """End the entire session and generate bill for remaining players"""
    session = db.query(Session).filter(Session.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.end_time:
        raise HTTPException(status_code=400, detail="Session already ended")
    
    # Get current players data
    import json
    current_players_data = []
    if hasattr(session, 'all_players_data') and session.all_players_data:
        try:
            current_players_data = json.loads(session.all_players_data)
        except:
            current_players_data = []
    
    # Calculate session duration and cost using current time (Pakistani time)
    pakistani_end_time = datetime.datetime.utcnow() + datetime.timedelta(hours=5)
    duration = pakistani_end_time - session.start_time
    total_minutes = int(duration.total_seconds() / 60)
    
    # Calculate total cost for all remaining players
    total_cost = 0
    total_discount = 0
    
    if current_players_data:
        for player in current_players_data:
            rate_type = player.get('rateType', 'hourly')
            rate_amount = player.get('rateAmount', session.base_rate or 0)
            
            # Convert rate_amount to float if it's a string
            if isinstance(rate_amount, str):
                try:
                    rate_amount = float(rate_amount)
                except ValueError:
                    rate_amount = 0.0
            
            player_type = player.get('playerType', 'guest')
            
            # Calculate base cost for this player
            player_cost = 0
            if rate_type == "hourly":
                # For hourly: calculate exact hours (no minimum)
                hours = total_minutes / 60
                player_cost = hours * rate_amount
            elif rate_type == "30min":
                # For 30min: calculate exact blocks (no minimum)
                blocks = total_minutes / 30
                player_cost = blocks * rate_amount
            elif rate_type == "time played" or rate_type == "time_played":
                # For time played: rate_amount is per minute
                player_cost = total_minutes * rate_amount
            
            # Apply discount only for members
            if player_type == 'member' and player.get('customerId'):
                customer = db.query(Customer).filter(Customer.id == player.get('customerId')).first()
                if customer and customer.discount > 0:
                    discount_amount = (player_cost * customer.discount) / 100
                    player_cost = player_cost - discount_amount
                    total_discount += discount_amount
            
            total_cost += player_cost
    
    # Update session with end time and calculated values
    session.end_time = pakistani_end_time
    session.total_minutes = total_minutes
    session.total_cost = total_cost
    session.discount = total_discount
    
    # Create bill for the session
    new_bill = Bill(
        session_id=session.id,
        date_issued=pakistani_end_time,
        paid=False,
        notes=f"Session ended - {len(current_players_data)} players completed"
    )
    db.add(new_bill)
    
    # Update table status to vacant
    table = db.query(Table).filter(Table.id == session.table_id).first()
    if table:
        table.status = TableStatusEnum.vacant
    
    db.commit()
    
    return {
        "message": "Session ended successfully",
        "session_id": session.id,
        "total_minutes": total_minutes,
        "total_cost": total_cost,
        "end_time": pakistani_end_time,
        "players_count": len(current_players_data),
        "bill_created": True
    }

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

# Billing endpoints
class BillOut(BaseModel):
    id: int
    session_id: int
    date_issued: datetime.datetime
    paid: bool
    notes: Optional[str] = None
    total_cost: Optional[float] = None
    customer_name: Optional[str] = None
    customer_contact: Optional[str] = None
    guest_name: Optional[str] = None
    guest_contact: Optional[str] = None
    table_name: Optional[str] = None
    table_id: Optional[int] = None
    start_time: Optional[datetime.datetime] = None
    end_time: Optional[datetime.datetime] = None
    total_minutes: Optional[int] = None
    base_rate: Optional[float] = None
    discount: Optional[float] = None
    discount_percentage: Optional[float] = None
    rate_type: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)

@app.get("/bills", response_model=List[BillOut])
def get_all_bills(db: DBSession = Depends(get_db)):
    """Get all bills with session and customer details"""
    bills = db.query(Bill).all()
    
    enriched_bills = []
    for bill in bills:
        bill_dict = {
            "id": bill.id,
            "session_id": bill.session_id,
            "date_issued": bill.date_issued,
            "paid": bill.paid,
            "notes": bill.notes,
            "total_cost": None,
            "customer_name": None,
            "customer_contact": None,
            "guest_name": None,
            "guest_contact": None,
            "table_name": None,
            "table_id": None,
            "start_time": None,
            "end_time": None,
            "total_minutes": None,
            "base_rate": None,
            "discount": None,
            "discount_percentage": None,
            "rate_type": None
        }
        
        # Get session details
        session = db.query(Session).filter(Session.id == bill.session_id).first()
        if session:
            bill_dict["total_cost"] = session.total_cost
            bill_dict["start_time"] = session.start_time
            bill_dict["end_time"] = session.end_time
            bill_dict["total_minutes"] = session.total_minutes
            bill_dict["base_rate"] = session.base_rate
            bill_dict["discount"] = session.discount
            bill_dict["guest_name"] = session.guest_name
            bill_dict["guest_contact"] = session.guest_contact
            bill_dict["rate_type"] = session.rate_type
            
            # Get table details
            table = db.query(Table).filter(Table.id == session.table_id).first()
            if table:
                bill_dict["table_name"] = table.table_name
                bill_dict["table_id"] = table.id
            
            # Get customer details if it's a member session
            if session.customer_id:
                print(f"DEBUG BILL CHECK: Session has customer_id = {session.customer_id}")
                customer = db.query(Customer).filter(Customer.id == session.customer_id).first()
                if customer:
                    print(f"DEBUG BILL CHECK: Found customer {customer.name}")
                else:
                    print(f"DEBUG BILL CHECK: Customer with ID {session.customer_id} not found!")
                if customer:
                    bill_dict["customer_name"] = customer.name
                    bill_dict["customer_contact"] = customer.contact_number
                    # Use customer's rate type if session doesn't have one
                    if not bill_dict["rate_type"]:
                        bill_dict["rate_type"] = customer.rate_type
                    # For member sessions, get discount from customer
                    if customer.discount > 0:
                        print(f"DEBUG BILL CHECK: Found customer {customer.name} with {customer.discount}% discount")
                        print(f"DEBUG BILL CHECK: Session customer_id = {session.customer_id}")
                        print(f"DEBUG BILL CHECK: Session rate_type = {session.rate_type}")
                        print(f"DEBUG BILL CHECK: Bill rate_type = {bill_dict['rate_type']}")
                        print(f"DEBUG BILL CHECK: Session total_minutes = {session.total_minutes}")
                        print(f"DEBUG BILL CHECK: Session base_rate = {session.base_rate}")
                        # Calculate the actual discount amount applied
                        if bill_dict["total_minutes"] and bill_dict["base_rate"]:
                            # Calculate what the base cost would be without discount
                            rate_type = bill_dict["rate_type"]
                            total_minutes = bill_dict["total_minutes"]
                            base_rate = bill_dict["base_rate"]
                            
                            # Convert base_rate to float if it's a string
                            if isinstance(base_rate, str):
                                try:
                                    base_rate = float(base_rate)
                                except ValueError:
                                    base_rate = 0.0
                            
                            base_cost = 0
                            
                            if rate_type == "hourly":
                                hours = total_minutes / 60
                                base_cost = hours * base_rate
                            elif rate_type == "30min":
                                blocks = total_minutes / 30
                                base_cost = blocks * base_rate
                            elif rate_type == "time played" or rate_type == "time_played":
                                base_cost = total_minutes * base_rate
                            
                            # Calculate discount amount
                            discount_amount = (base_cost * customer.discount) / 100
                            bill_dict["discount"] = discount_amount
                            bill_dict["discount_percentage"] = customer.discount
                            
                            # Recalculate total cost correctly
                            bill_dict["total_cost"] = base_cost - discount_amount
                            
                            print(f"DEBUG BILL: Customer {customer.name}")
                            print(f"DEBUG BILL: Duration {total_minutes}min, Rate {rate_type} ${bill_dict['base_rate']}")
                            print(f"DEBUG BILL: Base Cost ${base_cost}, Discount {customer.discount}% = ${discount_amount}")
                            print(f"DEBUG BILL: Final Cost ${bill_dict['total_cost']}")
            else:
                # For guest sessions, use guest name and contact
                if session.guest_name:
                    bill_dict["customer_name"] = session.guest_name
                    bill_dict["customer_contact"] = session.guest_contact or "N/A"
                # For guest sessions, ensure rate type is set and no discount
                if not bill_dict["rate_type"]:
                    bill_dict["rate_type"] = "hourly"  # Default for guests
                # Ensure guests have no discount
                bill_dict["discount"] = None
                bill_dict["discount_percentage"] = None
        
        enriched_bills.append(bill_dict)
    
    return enriched_bills

@app.post("/bills/{bill_id}/pay")
def mark_bill_as_paid(bill_id: int, db: DBSession = Depends(get_db)):
    """Mark a bill as paid"""
    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    
    if bill.paid:
        raise HTTPException(status_code=400, detail="Bill is already paid")
    
    bill.paid = True
    db.commit()
    
    return {"message": "Bill marked as paid successfully"}

@app.post("/fix-member-sessions")
def fix_member_sessions(db: DBSession = Depends(get_db)):
    """Fix all member sessions that have NULL customer_id"""
    # Get all member customers
    member_customers = db.query(Customer).filter(Customer.customer_type == "member").all()
    
    total_fixed = 0
    fixed_details = []
    
    for customer in member_customers:
        # Find sessions with guest_name matching customer name but customer_id=NULL
        sessions_to_fix = db.query(Session).filter(
            Session.guest_name == customer.name,
            Session.customer_id.is_(None)
        ).all()
        
        if sessions_to_fix:
            for session in sessions_to_fix:
                session.customer_id = customer.id
                session.guest_name = None  # Clear guest name since it's now a member session
                session.guest_contact = None  # Clear guest contact
                total_fixed += 1
            
            fixed_details.append(f"{customer.name}: {len(sessions_to_fix)} sessions")
    
    db.commit()
    
    return {
        "message": f"Fixed {total_fixed} member sessions total",
        "fixed_sessions": total_fixed,
        "details": fixed_details
    }

# Reports endpoints
@app.get("/reports/today")
def get_today_report(db: DBSession = Depends(get_db)):
    """Get today's player report"""
    # Get today's date in Pakistani time
    today = datetime.datetime.utcnow() + datetime.timedelta(hours=5)
    today_start = today.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    # Get all sessions that ended today
    sessions = db.query(Session).filter(
        Session.end_time >= today_start,
        Session.end_time <= today_end
    ).all()
    
    report_data = []
    for session in sessions:
        # Get player name
        player_name = "Guest"
        player_type = "guest"
        if session.customer_id:
            customer = db.query(Customer).filter(Customer.id == session.customer_id).first()
            if customer:
                player_name = customer.name
                player_type = "member"
        elif session.guest_name:
            player_name = session.guest_name
            player_type = "guest"
        
        # Get table name
        table_name = "Unknown"
        table = db.query(Table).filter(Table.id == session.table_id).first()
        if table:
            table_name = table.table_name
        
        report_data.append({
            "player_name": player_name,
            "player_type": player_type,
            "table_name": table_name,
            "start_time": session.start_time,
            "end_time": session.end_time,
            "amount_paid": session.total_cost or 0
        })
    
    return report_data

@app.get("/reports/custom-range")
def get_custom_range_report(
    start_date: str,
    end_date: str,
    player_type: str = "all",
    table_id: str = "all",
    rate_type: str = "all",
    db: DBSession = Depends(get_db)
):
    """Get custom date range report with filters"""
    # Parse dates
    start = datetime.datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.datetime.strptime(end_date, "%Y-%m-%d") + datetime.timedelta(days=1) - datetime.timedelta(seconds=1)
    
    # Convert to Pakistani time
    start = start + datetime.timedelta(hours=5)
    end = end + datetime.timedelta(hours=5)
    
    # Build query
    query = db.query(Session).filter(
        Session.end_time >= start,
        Session.end_time <= end
    )
    
    # Apply filters
    if player_type == "member":
        query = query.filter(Session.customer_id.isnot(None))
    elif player_type == "guest":
        query = query.filter(Session.customer_id.is_(None))
    
    if table_id != "all":
        query = query.filter(Session.table_id == int(table_id))
    
    if rate_type != "all":
        query = query.filter(Session.rate_type == rate_type)
    
    sessions = query.all()
    
    report_data = []
    for session in sessions:
        # Get player name
        player_name = "Guest"
        player_type = "guest"
        if session.customer_id:
            customer = db.query(Customer).filter(Customer.id == session.customer_id).first()
            if customer:
                player_name = customer.name
                player_type = "member"
        elif session.guest_name:
            player_name = session.guest_name
            player_type = "guest"
        
        # Get table name
        table_name = "Unknown"
        table = db.query(Table).filter(Table.id == session.table_id).first()
        if table:
            table_name = table.table_name
        
        report_data.append({
            "player_name": player_name,
            "player_type": player_type,
            "table_name": table_name,
            "rate_type": session.rate_type or "Unknown",
            "start_time": session.start_time,
            "end_time": session.end_time,
            "amount_paid": session.total_cost or 0
        })
    
    return report_data

@app.get("/reports/daily-summary")
def get_daily_summary_report(
    start_date: str,
    end_date: str,
    db: DBSession = Depends(get_db)
):
    """Get daily summary report"""
    # Parse dates
    start = datetime.datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.datetime.strptime(end_date, "%Y-%m-%d") + datetime.timedelta(days=1) - datetime.timedelta(seconds=1)
    
    # Convert to Pakistani time
    start = start + datetime.timedelta(hours=5)
    end = end + datetime.timedelta(hours=5)
    
    # Get all sessions in date range
    sessions = db.query(Session).filter(
        Session.end_time >= start,
        Session.end_time <= end
    ).all()
    
    # Group by date
    daily_data = {}
    for session in sessions:
        date_key = session.end_time.date()
        if date_key not in daily_data:
            daily_data[date_key] = {
                "total_players": 0,
                "total_duration": 0,
                "revenue": 0,
                "table_usage": {}
            }
        
        daily_data[date_key]["total_players"] += 1
        daily_data[date_key]["revenue"] += session.total_cost or 0
        
        # Calculate duration
        if session.start_time and session.end_time:
            duration = session.end_time - session.start_time
            daily_data[date_key]["total_duration"] += duration.total_seconds() / 60  # in minutes
        
        # Track table usage
        table_name = "Unknown"
        table = db.query(Table).filter(Table.id == session.table_id).first()
        if table:
            table_name = table.table_name
        
        if table_name not in daily_data[date_key]["table_usage"]:
            daily_data[date_key]["table_usage"][table_name] = 0
        daily_data[date_key]["table_usage"][table_name] += 1
    
    # Convert to list format
    summary_data = []
    for date, data in daily_data.items():
        # Find most active table
        most_active_table = max(data["table_usage"].items(), key=lambda x: x[1])[0] if data["table_usage"] else "None"
        
        # Format duration
        total_hours = int(data["total_duration"] // 60)
        total_mins = int(data["total_duration"] % 60)
        duration_str = f"{total_hours}h {total_mins}m" if total_hours > 0 else f"{total_mins}m"
        
        summary_data.append({
            "date": date.isoformat(),
            "total_players": data["total_players"],
            "total_duration": duration_str,
            "revenue": data["revenue"],
            "most_active_table": most_active_table
        })
    
    # Sort by date
    summary_data.sort(key=lambda x: x["date"])
    return summary_data

@app.get("/reports/table-usage")
def get_table_usage_report(
    start_date: str,
    end_date: str,
    db: DBSession = Depends(get_db)
):
    """Get table usage report"""
    # Parse dates
    start = datetime.datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.datetime.strptime(end_date, "%Y-%m-%d") + datetime.timedelta(days=1) - datetime.timedelta(seconds=1)
    
    # Convert to Pakistani time
    start = start + datetime.timedelta(hours=5)
    end = end + datetime.timedelta(hours=5)
    
    # Get all tables
    tables = db.query(Table).all()
    
    # Calculate total time period
    total_period_minutes = (end - start).total_seconds() / 60
    
    report_data = []
    for table in tables:
        # Get sessions for this table
        sessions = db.query(Session).filter(
            Session.table_id == table.id,
            Session.end_time >= start,
            Session.end_time <= end
        ).all()
        
        total_sessions = len(sessions)
        total_duration = 0
        
        for session in sessions:
            if session.start_time and session.end_time:
                duration = session.end_time - session.start_time
                total_duration += duration.total_seconds() / 60
        
        # Calculate usage percentage
        usage_percentage = (total_duration / total_period_minutes) * 100 if total_period_minutes > 0 else 0
        usage_percentage = min(usage_percentage, 100)  # Cap at 100%
        
        # Calculate idle time
        idle_minutes = total_period_minutes - total_duration
        idle_hours = int(idle_minutes // 60)
        idle_mins = int(idle_minutes % 60)
        idle_time = f"{idle_hours}h {idle_mins}m" if idle_hours > 0 else f"{idle_mins}m"
        
        # Format total duration
        total_hours = int(total_duration // 60)
        total_mins = int(total_duration % 60)
        duration_str = f"{total_hours}h {total_mins}m" if total_hours > 0 else f"{total_mins}m"
        
        report_data.append({
            "table_name": table.table_name,
            "total_sessions": total_sessions,
            "total_duration": duration_str,
            "idle_time": idle_time,
            "usage_percentage": round(usage_percentage, 1)
        })
    
    return report_data

 