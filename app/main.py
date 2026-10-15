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
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal, Optional, Annotated

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, Field, model_validator, StrictInt
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError


load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

Status = Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]
PresenseStatus = Literal["PRESENT", "WFH", "ON_DUTY"]
PRESENCE = ["PRESENT", "WFH", "ON_DUTY"]

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.employees.create_index("emp_code", unique = True)
    db.employees.create_index([("department", 1), ("emp_code", 1)])
    db.attendance_logs.create_index([("emp_code", 1),("date", 1)], unique=True)
    db.attendance_logs.create_index([("date", 11), ("emp_code", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("punch_in", -1)])
    yield

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifesapn = lifespan)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def round_half_up(value, places: int) -> float:
    q = Decimal(1).scaleb(-places)                      # 2 -> Decimal("0.01")
    return float(Decimal(value).quantize(q, rounding=ROUND_HALF_UP))


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    """R4: whole seconds / 3600, half-up to 2 decimals."""
    seconds = (punch_out - punch_in) // timedelta(seconds=1)
    return round_half_up(Decimal(seconds) / Decimal(3600), 2)


def compute_overtime(punch_out: datetime, shift_start: str, shift_end: str, date_str: str) -> int:
    """R3: floored minutes after shift_end, counted only if >= 30."""
    h, m = map(int, shift_end.split(":"))
    d = date.fromisoformat(date_str)
    end = datetime(d.year, d.month, d.day, h, m, tzinfo=IST)
    if shift_end <= shift_start:                        # overnight: shift ends next day
        end += timedelta(days=1)
    minutes = ((punch_out - end) // timedelta(seconds=1)) // 60
    return minutes if minutes >= 30 else 0


def compute_late_minutes(punch_in: datetime, shift_start: str, date_str: str) -> int:
    h, m = map(int, shift_start.split(":"))
    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    d = date.fromisoformat(date_str)
    minutes = int((punch_in - start).total_seconds() / 60)
    return minutes if minutes > 10 else 0


def derived_fields(emp: dict, date_str: str, status: str, punch_in, punch_out) -> dict:
    """The four server-computed fields, for any final state of a record."""
    if status not in PRESENCE:
        return {"work_hours": None, "late_minutes": 0, "overtime_minutes": 0, "half_day": False}
    late = compute_late_minutes(punch_in, emp["shift_start"], date_str)
    if punch_out is None:
        return {"work_hours": None, "late_minutes": late, "overtime_minutes": 0, "half_day": False}
    hours = compute_work_hours(punch_in, punch_out)
    return {
        "work_hours": hours,
        "late_minutes": late,
        "overtime_minutes": compute_overtime(punch_out, emp["shift_start"], emp["shift_end"], date_str),
        "half_day": hours < 4.5,                        # R5: the ROUNDED hours
    }


def whole_seconds(dt: datetime) -> datetime:
    return dt.replace(microsecond=0)


def from_ms(ms: int) -> datetime:
    return EPOCH + timedelta(milliseconds=ms)

def attendance_date(punch_in: datetime, shift_start: str, shift_end: str) -> str:

    local = punch_in.astimezone(IST)
    if shift_end <= shift_start:                    
        eh, em = map(int, shift_end.split(":"))
        if (local.hour, local.minute, local.second) < (eh, em, 0):
            local -= timedelta(days=1)                
    return local.date().isoformat()

def _ms_or_none(v):
    return to_ms(v) if isinstance(v, datetime) else v

def history_out(history) -> list:
    out = []
    for h in history or []:
        changes = {}
        for field, ch in h.get("changes", {}).items():
            if field in ("punch_in", "punch_out"):
                ch = {"from": _ms_or_none(ch.get("from")), "to": _ms_or_none(ch.get("to"))}
            changes[field] = ch
        out.append({"at": to_ms(h["at"]), "by": h["by"], "reason": h["reason"], "changes": changes})
    return out

def record_out(d: dict) -> dict:
    """Stored document -> API shape (AttendanceRecord). Tolerates legacy documents."""
    return {
        "emp_code": d["emp_code"],
        "date": d["date"],
        "status": d["status"],
        "punch_in": _ms_or_none(d.get("punch_in")),
        "punch_out": _ms_or_none(d.get("punch_out")),
        "work_hours": d.get("work_hours"),
        "late_minutes": d.get("late_minutes", 0),
        "overtime_minutes": d.get("overtime_minutes", 0),
        "half_day": d.get("half_day", False),
        "history": history_out(d.get("history")),
    }


# added new helpers
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
EpochMs = Annotated[StrictInt, Field(ge=100000000000, le=4102444800000)]

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
    punched_at: Optional[StrictInt] = Field(default=None, ge=100000000000, le=4102444800000)
    status: PresenseStatus = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMs] = None


class RegularizeIn(BaseModel):
    status: Optional[Status] = None
    punch_in: Optional[EpochMs] = None
    punch_out: Optional[EpochMs] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        client.admin.command("ping")        
    except Exception:
        raise HTTPException(503, "database unavailable")
    return {"status": "ok"}


# @app.post("/employees", status_code=201)
# def create_employee(body: EmployeeIn):                     #EmployeeIn is the Model class
#     if db.employees.find_one({"emp_code": body.emp_code}):     # this one not needed as 
#         raise HTTPException(409, "emp_code already exists")
#     doc = body.model_dump(mode="json")                                  # Argument -> model_dump(mode="json")
#     doc["created_at"] = datetime.now()      
#     db.employees.insert_one(doc)
#     doc.pop("_id", None)
#     return doc

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump(mode="json")             # Argument -> model_dump(mode="json")    
    now = datetime.now(timezone.utc)                 
    doc["created_at"] = now.replace(microsecond=now.microsecond // 1000 * 1000)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:                       
        raise HTTPException(409, "emp_code already exists")
    doc.pop("_id", None)                             
    doc["created_at"] = to_ms(doc["created_at"])      
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
    if date_from and date_to and date_from > date_to:
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


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")

    ts = from_ms(body.punched_at) if body.punched_at is not None else datetime.now(timezone.utc)
    ts = whole_seconds(ts)

    # most recent record whose punch_in is at or before punched_at (works across midnight)
    rec = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": ts}},
        sort=[("punch_in", -1)],
    )
    if not rec:
        raise HTTPException(404, "no punch-in found")
    if rec.get("punch_out") is not None:
        raise HTTPException(409, "already punched out")

    if ts <= rec["punch_in"] or ts - rec["punch_in"] > timedelta(hours=24):
        raise HTTPException(422, "punched_at must be after punch_in and within 24 hours of it")

    fields = derived_fields(emp, rec["date"], rec["status"], rec["punch_in"], ts)
    updated = db.attendance_logs.find_one_and_update(
        {"_id": rec["_id"], "punch_out": None},         # atomic: only if still open
        {"$set": {"punch_out": ts, **fields}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:                                  # someone else closed it first
        raise HTTPException(409, "already punched out")
    return record_out(updated)


TRACKED = ["status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day"]


@app.patch("/attendance/{emp_code}/{date}")
def regularize(
    body: RegularizeIn,
    emp_code: str,
    date: str = Path(pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(422, "date must be a real calendar date, YYYY-MM-DD")

    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")
    rec = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not rec:
        raise HTTPException(404, "no record for that date")

    new_status = body.status or rec["status"]
    in_supplied = body.punch_in is not None
    out_supplied = body.punch_out is not None

    if new_status in PRESENCE:
        new_in = whole_seconds(from_ms(body.punch_in)) if in_supplied else rec.get("punch_in")
        new_out = whole_seconds(from_ms(body.punch_out)) if out_supplied else rec.get("punch_out")
        if new_in is None:
            raise HTTPException(422, "a presence status requires a punch_in")
        if in_supplied and attendance_date(new_in, emp["shift_start"], emp["shift_end"]) != date:
            raise HTTPException(422, "punch_in must stay on the record's attendance date")
        if new_out is not None and (in_supplied or out_supplied):
            if new_out <= new_in or new_out - new_in > timedelta(hours=24):
                raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")
    else:                                                # ABSENT / LEAVE clear the punch times
        if in_supplied or out_supplied:
            raise HTTPException(422, "ABSENT/LEAVE clear the punch times; do not supply them")
        new_in = new_out = None

    new_vals = {"status": new_status, "punch_in": new_in, "punch_out": new_out,
                **derived_fields(emp, date, new_status, new_in, new_out)}
    old_vals = {
        "status": rec["status"], "punch_in": rec.get("punch_in"), "punch_out": rec.get("punch_out"),
        "work_hours": rec.get("work_hours"), "late_minutes": rec.get("late_minutes", 0),
        "overtime_minutes": rec.get("overtime_minutes", 0), "half_day": rec.get("half_day", False),
    }
    changes = {f: {"from": old_vals[f], "to": new_vals[f]} for f in TRACKED if old_vals[f] != new_vals[f]}
    if not changes:
        raise HTTPException(422, "the request changes nothing")

    now = datetime.now(timezone.utc)
    entry = {"at": now.replace(microsecond=now.microsecond // 1000 * 1000),   # Mongo keeps milliseconds
             "by": body.regularized_by, "reason": body.reason, "changes": changes}

    n_history = len(rec.get("history") or [])
    updated = db.attendance_logs.find_one_and_update(
        {   # optimistic lock: apply only if the record is exactly as we read it
            "_id": rec["_id"],
            "status": old_vals["status"],
            "punch_in": old_vals["punch_in"],
            "punch_out": old_vals["punch_out"],
            f"history.{n_history}": {"$exists": False},
        },
        {"$set": new_vals, "$push": {"history": entry}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "the record was changed by someone else; retry")
    return record_out(updated)


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")

    year, mon = int(month[:4]), int(month[5:])
    last_day = calendar.monthrange(year, mon)[1]
    days = [date(year, mon, n) for n in range(1, last_day + 1)]
    weekend = [d.isoformat() for d in days if d.weekday() >= 5]          # Sat, Sun
    joined = date.fromisoformat(emp["joined_on"])
    working_days = sum(1 for d in days if d.weekday() < 5 and d >= joined)   # R7

    pipeline = [
        {"$match": {"emp_code": emp_code,
                    "date": {"$gte": days[0].isoformat(), "$lte": days[-1].isoformat()}}},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {"$cond": [
                {"$in": ["$date", weekend]},                 # weekend records never count as present
                0,
                {"$cond": [
                    {"$in": ["$status", PRESENCE]},
                    {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 0.5, 1]},
                    0,
                ]},
            ]}},
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
        }},
    ]
    agg = next(iter(db.attendance_logs.aggregate(pipeline)), None) or {}   # no records -> {}

    present = float(agg.get("present_days", 0))
    pct = round_half_up(Decimal(str(present)) / Decimal(working_days) * 100, 2) if working_days else None
    return {
        "emp_code": emp_code, "month": month, "working_days": working_days, "present_days": present,
        "leave_days": int(agg.get("leave_days", 0)), "late_count": int(agg.get("late_count", 0)),
        "total_late_minutes": int(agg.get("total_late_minutes", 0)),
        "total_overtime_minutes": int(agg.get("total_overtime_minutes", 0)),
        "attendance_pct": pct,
    }










# --------------------------------------------------------------------------- #
