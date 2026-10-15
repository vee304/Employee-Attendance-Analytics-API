"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta, timezone
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, Field, model_validator, StrictInt
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError


load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

Status = Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]
PresenseStatus = Literal["PRESENT", "WFH", "ON_DUTY"]

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.employees.create_index("emp_code", unique = True)
    db.employees.create_index([("department", 1), ("emp_code", 1)])
    db.attendance_logs.create_index([("emp_code", 1),("date", 1)], unique=True)
    db.attendance_logs.create_index([("date", 11), ("emp_code", 1)])
    yield

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifesapn = lifespan)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def from_ms(ms: int) -> datetime:
    return EPOCH + timedelta(milliseconds=ms)

def attendance_date(punch_in: datetime, shift_start: str, shift_end: str) -> str:

    local = punch_in.astimezone(IST)
    if shift_end <= shift_start:                    
        eh, em = map(int, shift_end.split(":"))
        if (local.hour, local.minute, local.second) < (eh, em, 0):
            local -= timedelta(days=1)                
    return local.date().isoformat()


def compute_late_minutes(punch_in: datetime, shift_start: str, date_str: str) -> int:
    h, m = map(int, shift_start.split(":"))
    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    d = date.fromisoformat(date_str)
    minutes = int((punch_in - start).total_seconds() / 60)
    return minutes if minutes > 10 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    return round((punch_out - punch_in).total_seconds() / 3600, 2)


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    return max(0, int((punch_out - end).total_seconds() / 60))


# added new helpers
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

def to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - EPOCH) // timedelta(milliseconds=1)

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class EmployeeIn(BaseModel):
    emp_code: str = Field(..., pattern=r'^EMP\d{4,6}$')                                      
    name: str = Field(min_length=1, max_length=100)                           # set the min and max length 
    email: str = Field(max_length=120, pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$')  # set the max length and pattern for email
    department: str = Field(min_length= 1, max_length=50)                     # set the min and max lenght
    shift_start: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$', default='09:30', description="24-hour HH:MM, IST.")
    shift_end: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$', default='18:30', description="24-hour HH:MM, IST. <= shift_start means an overnight shift.")
    joined_on: date = Field(description="First day of employment, YYYY-MM-DD.")      # date format is still left

    ## because we need to make sure shift_end and shift_start has to differ
    @model_validator(mode="after")
    def check_validiti(self):
        if self.shift_start == self.shift_end:
            raise ValueError("The shift start and end value has to differ")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):                     #EmployeeIn is the Model class
    if db.employees.find_one({"emp_code": body.emp_code}):     # this one not needed as 
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump(mode="json")                                  # Argument -> model_dump(mode="json")
    doc["created_at"] = datetime.now()      
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    q = {}                             # it is a MongoDB filter -> {} means "match everything"
    if department:
        q["department"] = department
    skip = (page-1)*page_size          # logic for pagination
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).sort("emp_code", 1).skip(skip).limit(page_size)) # added sort()

    for e in items:
        e["created_at"] = to_ms(e["created_at"])
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")
    
    ts = datetime.fromtimestamp(body.punched_at / 1000) if body.punched_at is not None else datetime.now(timezone.utc)
    ts = ts.replace(microsecond = 0)
    d = ts.date().isoformat()
    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(409, "already punched in for this date")
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    res = db.attendance_logs.insert_one(doc)
    doc["id"] = str(res.inserted_id)
    doc.pop("_id", None)
    return doc


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(1, ge=1, le=100),
):
    if date_from and date_to and date_from > date_to;
        raise HTTPException(422, "date_form must not be after date_to")
    
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from.isoformat()
        if date_to:
            q["date"]["$lte"] = date_to.isoformat()
    if status:
        q["status"] = status

    
    total = db.attendance_logs.count_documents(q)
    cursor = (
        db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip((page-1) * page_size).limit(page_size)
    )
    
    return {"items": [record_out(d) for d in cursor], "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
