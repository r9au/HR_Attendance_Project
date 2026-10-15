import os
from datetime import datetime, timedelta, timezone, date
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from pymongo import MongoClient
import calendar

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    h, m = map(int, shift_start.split(":"))
    punch_in=punch_in.astimezone(IST) #f4
    start = (punch_in).replace(hour=h, minute=m, second=0, microsecond=0)
    minutes = int((punch_in - start).total_seconds() // 60)
    return minutes if minutes > 10 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    if punch_in.tzinfo is None:
        punch_in = punch_in.replace(tzinfo=timezone.utc)

    if punch_out.tzinfo is None:
        punch_out = punch_out.replace(tzinfo=timezone.utc)
    hrs=(punch_out - punch_in).total_seconds() / 3600
    if hrs<=0 or hrs>24:
        raise HTTPException(400, "Bad request")
    return round(hrs, 2)


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    punch_out=punch_out.astimezone(IST) #f5
    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    overtime=max(0, int((punch_out - end).total_seconds() / 60))
    return overtime if overtime>=30 else 0


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

class PunchedOut(BaseModel):
    emp_code: str
    punched_out: Optional[int] = None

class AttendanceUpdate(BaseModel):
    by:str
    punch_in: Optional[str]  
    date:Optional[str] 
    punch_out: Optional[int] 
    work_hours: Optional[float]
    late_minutes: Optional[int]
    overtime_minutes: Optional[int]
    half_day: Optional[bool]
    status:Optional[str]
    reason:Optional[str]



# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Employee Enpoints
# --------------------------------------------------------------------------- #

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc) #f1
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {}
    if department:
        q["department"] = department
    skip = (page-1) * page_size #f2
    total = db.employees.count_documents({})
    items = list(db.employees.find(q, {"_id": 0}).skip(skip).limit(page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}

# --------------------------------------------------------------------------- #
# Attendance Endpoints
# --------------------------------------------------------------------------- #

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    ts = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc) if body.punched_at else datetime.now(tz=timezone.utc) #f3
    d = ts.astimezone(IST).date().isoformat()
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
    page: int = 1,
    page_size: int = 20,
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    docs = list(db.attendance_logs.find(q))
    docs.sort(key=lambda d: d["date"], reverse=True)
    total = len(docs)
    page_docs = docs[(page - 1) * page_size : page * page_size]
    for d in page_docs:
        d["id"] = str(d.pop("_id"))
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size}

@app.patch("/attendance/punch-out", status_code=201)
def punch_out(body: PunchedOut):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404,"employee not found")
    ts = datetime.fromtimestamp(body.punched_out / 1000, tz=timezone.utc) if body.punched_out else datetime.now(tz=timezone.utc) #f3
    d = ts.astimezone(IST).date().isoformat()
    print(d)
    log=db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d})
    if not log:
        raise HTTPException(404," no log found for this date")
    overtime=compute_overtime(ts,emp["shift_end"],d)
    work_hrs=compute_work_hours(log["punch_in"],ts)
    half_day=False
    if work_hrs<4.5:
        half_day=True
    try:
        db.attendance_logs.update_one(
            {"_id":log["_id"]},
            {
                "$set":{
                    "punch_out":ts,
                    "work_hours":work_hrs,
                    "half_day":half_day,
                    "overtime_minutes":overtime
                }
            }
        )
        return "Punched out successfully"
    except Exception as e:
        raise HTTPException(500,e)

@app.patch("/attendance", status_code=200) 
def update_record(emp_code: str,date_str:str,body:AttendanceUpdate):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404,"employee not found")
    try:
        d = date.fromisoformat(date_str).isoformat()
    except ValueError:
        raise HTTPException(422,"Invalid date")
    log=db.attendance_logs.find_one({"emp_code": emp_code, "date": d})
    if not log:
        raise HTTPException(404," no log found for this date")
    req=body.model_dump(exclude_unset=True)
    if req.get("status") in {"ABSENT", "LEAVE"}:
            if "punch_in" not in req or "punch_out" not in req:
                    raise HTTPException(
                        status_code=422,
                        detail="ABSENT and LEAVE require punch_in and punch_out to be explicitly null"
                    )
            if req["punch_in"] is not None or req["punch_out"] is not None:
                    raise HTTPException(
                        status_code=422,
                        detail="punch_in and punch_out must be null for ABSENT and LEAVE"
                    )
            req["overtime_minutes"]=0
            req["late_minutes"]=0
            req["work_hours"]=0
            req["half_day"] = False
    rec=dict(log)
    changes={}
    for field, update in req.items():
        if field in {"by", "reason"}:
            continue
        if req.get("status") in {"ABSENT", "LEAVE"} and field in {
        "punch_in", "punch_out"
        }:
            value = rec.get(field)

            if value is not None:
                changes[field] = {
                    "from": value,
                    "to": None
                }
                continue
        if update is None:
            continue

        value = rec.get(field)

        if value != update:
            changes[field] = {
                "from": value,
                "to": update
            }

    rec_update = {
        field: change["to"]
        for field, change in changes.items()
    }

    if rec_update:
        db.attendance_logs.update_one(
            {"_id": log["_id"]},
            {"$set": rec_update}
        )
    
    db.attendance_logs.update_one(
            {"_id":log["_id"]},
            {
                "$push":{
                    "history":{
                         "at": {
                            "$date": datetime.now(tz=timezone.utc)
                            },
                        "by":req["by"],
                        "reason":req["reason"],
                        "changes":changes
                    }
                }
            }
        )
    return {"message":"log updated successfully","changes":changes}    

# --------------------------------------------------------------------------- #
# Analytics Endpoints
# --------------------------------------------------------------------------- #          

@app.get("/analytics/employees/{emp_code}/monthly", status_code=200)
def monthly_analytics(emp_code: str,month:str):
    try:
        month_date=datetime.strptime(month,"%Y-%m")
        if month_date.strftime("%Y-%m") != month:
            raise ValueError()
    except ValueError:
        raise HTTPException(422,"Invalid month")
    print(emp_code)
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404,"employee not found")
    month_start=month_date.date()
    month_end=month_start.replace(
        day=calendar.monthrange(month_start.year,month_start.month)[1]
    )

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte":  max(
                month_start,
                date.fromisoformat(emp["joined_on"])
            ).isoformat(),
                    "$lte": month_end.isoformat()
                }
            }
        },
        {
            "$set":{
                "is_present":{
                    "$in":[{"$toLower":"$status"},["present","wfh","on_duty"]]
                }
            }
        },
        {
    "$group": {
        "_id": None,
        "present_days": {
    "$sum": {
        "$cond": [
            {
                "$and": [
                    "$is_present",
                    {
                        "$in": [
                            {
                                "$dayOfWeek": {
                                    "$dateFromString": {
                                        "dateString": "$date"
                                    }
                                }
                            },
                            [2, 3, 4, 5, 6]
                        ]
                    }
                ]
            },
            {
                "$cond": [
                    {"$eq": ["$half_day", True]},
                    0.5,
                    1
                ]
            },
            0
        ]
    }
},
        "leave_days": {
            "$sum": {
                "$cond": [
                    {"$eq": [
                        {"$toLower": {"$ifNull": ["$status", ""]}},
                        "leave"
                    ]},
                    1,
                    0
                ]
            }
        },
        "late_count": {
            "$sum": {
                "$cond": [
                    {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                    1,
                    0
                ]
            }
        },
        "total_late_minutes": {
            "$sum": {"$ifNull": ["$late_minutes", 0]}
        },
        "total_overtime_minutes": {
            "$sum": {"$ifNull": ["$overtime_minutes", 0]}
        }
    }
}
    ]

    result = list(db.attendance_logs.aggregate(pipeline))
    if not result:
        raise HTTPException(404, "no logs found")
    summary = result[0]
    start_date=max(month_start,date.fromisoformat(emp["joined_on"]))
    work_days=0
    if start_date<=month_end:
        total_days=(month_end - start_date).days + 1
        for i in range(total_days):
            current_date=start_date+timedelta(days=i)
            if current_date.weekday()<5:
                work_days+=1

    if work_days > 0:
        attendance_pct = round(
            summary["present_days"] / work_days * 100, 2
        )
    else:
        attendance_pct = None
    return {
        "description":"summary",
        "emp_code": emp_code,
        "month": month,
        "working_days": work_days,
        "present_days": summary["present_days"],
        "leave_days": summary["leave_days"],
        "late_count": summary["late_count"],
        "total_late_minutes": summary["total_late_minutes"],
        "total_overtime_minutes": summary["total_overtime_minutes"],
        "attendance_pct": attendance_pct
        }

@app.get("/analytics/departments/summary", status_code=200)
def department_monthly_analytics(month:str,department: str|None=None):
    try:
        month_date=datetime.strptime(month,"%Y-%m")
        if month_date.strftime("%Y-%m") != month:
            raise ValueError()
    except ValueError:
         raise HTTPException(422,"Invalid month")
    month_start=month_date.date()
    month_end=month_start.replace(
        day=calendar.monthrange(month_start.year,month_start.month)[1]
    )
    start_str=month_start.isoformat()
    end_str=month_end.isoformat()
    match_filter={"joined_on": {"$lte": end_str}}
    if department is not None:
        match_filter["department"] = department
    pipeline=[
        {
            "$match":match_filter
        },
        {
            "$lookup":{
                "from":"attendance_logs",
                "let":{"code":"$emp_code"},
                "pipeline":[
                    {
                        "$match":{
                            "$expr":{
                                "$and":[
                                    {"$eq":["$emp_code","$$code"]},
                                    {"$gte":["$date",start_str]},
                                    {"$lte":["$date",end_str]}
                                ]
                            }
                        }
                    }
                ],
                "as":"logs"
            }
        },
        {
            "$unwind":{
                "path":"$logs",
                "preserveNullAndEmptyArrays":True
            }
        },
        {
            "$group":{
                "_id":{
                    "emp_code":"$emp_code",
                    "department":"$department"
                },
                "present_days":{
                    "$sum":{
                        "$cond":[
                            {
                            "$and":[
                                {"$in":[
                                    {"$toLower":{
                                        "$ifNull":["$logs.status",""]
                                    }},
                                    ["present","wfh","on_duty"]
                                ]},
                                {
                                "$in":[
                                    {
                                        "$dayOfWeek":{
                                            "$dateFromString":{
                                                "dateString":"$logs.date",
                                                "onError": None,
                                                "onNull": None
                                            }
                                        }
                                    },
                                    [2,3,4,5,6]
                                ]
                            }
                            ]
                            },
                            {
                                "$cond":[
                                    {"$eq":["$logs.half_day",True]},
                                    0.5,1
                                ]
                            },0
                        ]
                    }
                },
                "late_count":{
                    "$sum":{
                        "$cond":[
                            {"$gt":[
                                {"$ifNull":["$logs.late_minutes",0]},0
                            ]},
                            1,0
                        ]
                    }
                },
                "total_late_minutes":{
                     "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
                },
                "leave_count":{
                    "$sum":{
                        "$cond":[
                            {"$eq":[
                                {"$toLower":{
                                    "$ifNull":["$logs.status",""]
                                }},
                                "leave"
                            ]},
                            1,0
                        ]
                    }
                },
                "on_duty_count":{
                    "$sum":{
                        "$cond":[
                            {"$eq":[
                                {"$toLower":{
                                    "$ifNull":["$logs.status",""]
                                }},
                                "on_duty"
                            ]},
                            1,0
                        ]
                    }
                },
                "work_hours_total": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            {
                                                "$toLower": {
                                                    "$ifNull": ["$logs.status", ""]
                                                }
                                            },
                                            ["present", "wfh", "on_duty"]
                                        ]
                                    },
                                    {"$ne": [{"$ifNull": ["$logs.work_hours", None]}, None]}
                                ]
                            },
                            "$logs.work_hours",
                            0
                        ]
                    }
                },
                "work_hours_records":{
                    "$sum": {
        "$cond": [
            {
                "$and": [
                    {
                        "$in": [
                            {
                                "$toLower": {
                                    "$ifNull": ["$logs.status", ""]
                                }
                            },
                            ["present", "wfh", "on_duty"]
                        ]
                    },
                    {"$ne": [{"$ifNull": ["$logs.work_hours", None]}, None]}
                ]
            },
            1,
            0
        ]
    }
                }
            }
        },
        {
            "$group":{
            "_id":"$_id.department",
            "headcount":{"$sum":1},
            "present_days": {"$sum": "$present_days"},
            "late_count": {"$sum": "$late_count"},
            "total_late_minutes": {
                "$sum": "$total_late_minutes"
            },
            "leave_count": {"$sum": "$leave_count"},
            "on_duty_count": {"$sum": "$on_duty_count"},
            "work_hours_total": {"$sum": "$work_hours_total"},
            "work_hours_records": {"$sum": "$work_hours_records"}
            }
        },
        {
            "$project":{
                "_id": 0,
            "department": "$_id",
            "headcount": 1,
            "present_days": 1,
            "late_count": 1,
            "total_late_minutes": 1,
            "leave_count": 1,
            "on_duty_count": 1,
            "avg_work_hours":{
                "$cond":[
                    {"$gt": ["$work_hours_records", 0]},
                    {
                        "$divide": [
                            "$work_hours_total",
                            "$work_hours_records"
                        ]
                    },
                    None
                ]
            }
            }
        },
        {
        "$sort": {"department": 1}
    }
    ]
    result=list(db.employees.aggregate(pipeline))
    return {"month":month, "description":"summary","items":result}

@app.get("/analytics/leaderboard/late", status_code=200)
def analytics_leaderboard(month:str,limit:int=Query(default=10,ge=1,le=50),department: str|None=None):
     try:
        month_date=datetime.strptime(month,"%Y-%m")
        if month_date.strftime("%Y-%m") != month:
            raise ValueError()
     except ValueError:
             raise HTTPException(422,"Invalid month")
     
     month_start=month_date.date()
     month_end=month_start.replace(
            day=calendar.monthrange(month_start.year,month_start.month)[1]
        )
     start_str=month_start.isoformat()
     end_str=month_end.isoformat()
     pipeline=[
         {
             "$match": {
            "date": {
                "$gte": start_str,
                "$lte": end_str
            }
        }
         },
         {
             "$group": {
            "_id": "$emp_code",
            "total_late_minutes": {
                "$sum": {"$ifNull": ["$late_minutes", 0]}
            }
        }
         },
         {
             "$match":{
                 "total_late_minutes":{"$gt":0}
             }
         },
         {
             "$lookup":{
                 "from":"employees",
                 "localField":"_id",
                 "foreignField":"emp_code",
                 "as":"employee"
             }
         },
         {
             "$unwind":"$employee"
         },
         {
             "$set":{
                 "emp_code":"$_id",
                 "name":"$employee.name",
                 "department": "$employee.department"
             }
         },
         {
             "$setWindowFields":{
                 "sortBy":{"total_late_minutes":-1},
                 "output":{
                     "rank":{"$rank":{}}
                 }
             }
         },
         {
             "$match":{
                 "rank":{"$lte":limit}
             }
         },
         {
             "$project":{
            "_id": 0,
            "rank": 1,
            "emp_code": 1,
            "name": 1,
            "department": 1,
            "total_late_minutes": 1
        }
         },
         {
             "$sort": {
            "total_late_minutes": -1,
            "emp_code": 1
            }
         }
     ]
     if department is not None:
         pipeline.insert(6,{"$match": {"department": department}})
     result=list(db.attendance_logs.aggregate(pipeline))
     return {"month":month,"description":"Leaderboard","items":result}

@app.get("/analytics/departments/{department}/trend", status_code=200)
def analytics_leaderboard(department: str,from_date: date = Query(alias="from"),to_date: date = Query(alias="to")):
    if to_date<from_date:
        raise HTTPException(422,"to must be on or after from")
    if (to_date-from_date).days+1>92:
        raise HTTPException(422,"Date range cannot exceed 92 days")
    if not db.employees.find_one({"department": department}):
        raise HTTPException(404,"Department not found")
    start_str=from_date.isoformat()
    end_str=to_date.isoformat()
    start_dt=datetime.combine(from_date,datetime.min.time(),tzinfo=timezone.utc)
    end_dt=datetime.combine(to_date + timedelta(days=1),datetime.min.time(),tzinfo=timezone.utc)
    pipeline=[
        {
        "$documents": [
            {"date": start_dt}
        ]
        },
        {
        "$densify": {
            "field": "date",
            "range": {
                "step": 1,
                "unit": "day",
                "bounds": [start_dt, end_dt]
            }
        }
        },
        {
        "$set": {
            "date_str": {
                "$dateToString": {
                    "format": "%Y-%m-%d",
                    "date": "$date",
                    "timezone": "UTC"
                }
            }
        }
    },
    {
        "$lookup": {
            "from": "employees",
            "let": {"day": "$date_str"},
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                {"$eq": ["$department", department]},
                                {"$lte": ["$joined_on", "$$day"]}
                            ]
                        }
                    }
                }
            ],
            "as": "dept_employees"
        }
    },
    {
        "$lookup": {
            "from": "attendance_logs",
            "let": {
                "day": "$date_str",
                "codes": {
                    "$map": {
                        "input": "$dept_employees",
                        "as": "emp",
                        "in": "$$emp.emp_code"
                    }
                }
            },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                {"$eq": ["$date", "$$day"]},
                                {"$in": ["$emp_code", "$$codes"]}
                            ]
                        }
                    }
                }
            ],
            "as": "logs"
        }
    },
    {
        "$set": {
            "is_working_day": {
                "$in": [
                    {"$dayOfWeek": "$date"},
                    [2, 3, 4, 5, 6]
                ]
            },
            "headcount": {"$size": "$dept_employees"},

            "present_count": {
                "$sum": {
                    "$map": {
                        "input": {
                            "$filter": {
                                "input": "$logs",
                                "as": "log",
                                "cond": {
                                    "$in": [
                                        {
                                            "$toLower": {
                                                "$ifNull": [
                                                    "$$log.status", ""
                                                ]
                                            }
                                        },
                                        ["present", "wfh", "on_duty"]
                                    ]
                                }
                            }
                        },
                        "as": "log",
                        "in": {
                            "$cond": [
                                {"$eq": ["$$log.half_day", True]},
                                0.5,
                                1
                            ]
                        }
                    }
                }
            },

            "late_count": {
                "$size": {
                    "$filter": {
                        "input": "$logs",
                        "as": "log",
                        "cond": {
                            "$gt": [
                                {
                                    "$ifNull": [
                                        "$$log.late_minutes", 0
                                    ]
                                },
                                0
                            ]
                        }
                    }
                }
            }
        }
    },
    {
        "$set": {
            "attendance_rate": {
                "$cond": [
                    {
                        "$and": [
                            "$is_working_day",
                            {"$gt": ["$headcount", 0]}
                        ]
                    },
                    {
                        "$divide": [
                            {
                                "$floor": {
                                    "$add": [
                                        {
                                            "$multiply": [
                                                {
                                                    "$divide": [
                                                        "$present_count",
                                                        "$headcount"
                                                    ]
                                                },
                                                10000
                                            ]
                                        },
                                        0.5
                                    ]
                                }
                            },
                            10000
                        ]
                    },
                    None
                ]
            }
        }
    },
    {
        "$setWindowFields": {
            "sortBy": {"date": 1},
            "output": {
                "moving_avg_raw": {
                    "$avg": "$attendance_rate",
                    "window": {
                        "documents": [-6, 0]
                    }
                }
            }
        }
    },
    {
        "$set": {
            "moving_avg_7d": {
                "$cond": [
                    {"$ne": ["$moving_avg_raw", None]},
                    {
                        "$divide": [
                            {
                                "$floor": {
                                    "$add": [
                                        {
                                            "$multiply": [
                                                "$moving_avg_raw",
                                                10000
                                            ]
                                        },
                                        0.5
                                    ]
                                }
                            },
                            10000
                        ]
                    },
                    None
                ]
            }
        }
    },
    {
        "$project": {
            "_id": 0,
            "date": "$date_str",
            "is_working_day": 1,
            "headcount": 1,
            "present_count": 1,
            "late_count": 1,
            "attendance_rate": 1,
            "moving_avg_7d": 1
        }
    },
    {
        "$sort": {"date": 1}
    }
    ]
    result=list(db.aggregate(pipeline))
    result = list(db.aggregate(pipeline))
    return {
        "department": department,
        "from": start_str,
        "to": end_str,
        "items": result
    }

    
    



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
