
# Attendance API
Run: uvicorn app.main:app --port 8000
Set MONGO_URI and MONGO_DB environment variables.


## Implemented
   - /health, /employees (POST, GET), /attendance (GET)
   - /attendance/punch-in, /attendance/punch-out, PATCH /attendance/{emp_code}/{date}
   - /analytics/employees/{emp_code}/monthly

## Not implemented
   - GET   /analytics/departments/summary
   - GET   /analytics/leaderboard/late
   - GET   /analytics/departments/{department}/trend
   - GET   /admin/explain/{endpoint}
