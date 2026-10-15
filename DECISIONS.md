# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?

`employees.emp_code` (unique) enforces unique codes and serves every employee lookup. `employees(department, emp_code)` serves the department filter and its sort. `attendance_logs(emp_code, date)` (unique) allows one record per employee per day and serves PATCH, monthly and attendance filters. `attendance_logs(date desc, emp_code)` matches the list sort. `attendance_logs(emp_code, punch_in desc)` finds the latest punch-in for punch-out. Rejected: a separate `department` index, because the compound one already covers it.

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.

Both requests build the same record and call `insert_one`, with no check beforehand. The unique index on `(emp_code, date)` lets the database accept the first and raise `DuplicateKeyError` for the second, which I return as 409. Exactly one gets 201.


3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?
- Not implemented

4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
- Not implemented

5. **One thing you would change** if this had to serve 100x the data.


# Not Done

   GET   /analytics/departments/summary
   GET   /analytics/leaderboard/late
   GET   /analytics/departments/{department}/trend
   GET   /admin/explain/{endpoint}