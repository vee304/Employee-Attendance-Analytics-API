
# Attendance API
Run: uvicorn app.main:app --port 8000
Set MONGO_URI and MONGO_DB environment variables.


## Implemented
   - /health, /employees (POST, GET), /attendance (GET)
   - /attendance/punch-in, /attendance/punch-out, PATCH /attendance/{emp_code}/{date}
   - /analytics/employees/{emp_code}/monthly

## Not implemented
   - (list here: departments summary, late leaderboard, department trend, admin explain)