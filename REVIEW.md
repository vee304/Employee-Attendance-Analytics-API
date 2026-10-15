# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| #   | Where (function / line) | What is wrong             | How you'd notice it (test, input, or symptom)                                                            | How you fixed it |
| --- | ----------------------- | ------------------------- | -------------------------------------------------------------------------------------------------------- | ---------------- |
| 1   | list_employees, skip = page * page_size | Skips one whole page too many. Page 1 skips the first 20 employees. | Upon running server with 8000/employees, returned items: [] while total was 6.| skip = (page - 1) * page_size. |

| 2   | list_employees, count_documents({}) | total ignores the department filter, but R10 says total reflects the applied filters | ?department=Sales returns 2 items but total 6| count_documents(q), with the same filter q used by find |

| 3  | list_employees, query parameters | page and page_size are unchecked. page=0 and page_size=100000 are accepted. The contract allows page >= 1 and 1 <= page_size <= 100, otherwise 422. | ?page=0 and ?page_size=101 return 200 | Query(1, ge=1) and Query(20, ge=1, le=100) |

| 4  |	list_employees, no sort |The contract says sorted by emp_code ascending. Order was whatever MongoDB returned. | Insert employees out of order and list them | .sort("emp_code", 1) before skip and limit |

| 5  | 	list_employees and create_employee, created_at |create_employee, created_at	Returned as an ISO date string. The Employee schema requires an integer in epoch milliseconds | Compare the response with the schema | to_ms() helper, applied to the response only |

| 6 | `create_employee`, input validation (`EmployeeIn`) | The request model accepts anything. `emp_code`, email and shift times take any string, `joined_on` is any string, and `shift_start == shift_end` is accepted. The contract's `EmployeeCreate` has patterns, length limits and a date format. | POST `emp_code: "abc"` or `joined_on: "banana"` returned 201. | `Field` patterns and lengths from the schema, `joined_on: date`, and a `model_validator` that rejects equal shifts. `model_dump(mode="json")` stores `joined_on` as a `YYYY-MM-DD` string as `DATA_MODEL.md` requires. |

| 7 | `create_employee`, find then insert | Check-then-insert race: two simultaneous requests can both pass the check and create duplicate `emp_code` values. The contract requires a database unique index, and the app created no indexes at all. | Fire two identical POSTs in parallel and look in the database. `getIndexes()` showed only `_id_`. | Unique index on `emp_code`, created at startup, and the `DuplicateKeyError` raised by the insert is caught and returned as 409. |

| 8 | `create_employee`, `created_at` stored value | `datetime.now()` is naive local time. The data model wants a UTC datetime in MongoDB. | Stored value shifts by the machine's UTC offset. | `datetime.now(timezone.utc)`, cut to milliseconds, stored as a datetime, returned as epoch ms. |

| 9 | `punch_in`, input validation (`PunchInIn`) | `status` accepts any string. `punched_at` accepts any int, including seconds, negatives and 0. | POST punch-in with `status: "banana"` or `punched_at: 1783570200` returned 201. | `status: Literal["PRESENT","WFH","ON_DUTY"]` and `punched_at` as `StrictInt` within the `EpochMillis` range. |

| 10 | `punch_in`, unknown employee | `emp` is `None`, so `emp["shift_start"]` raises `TypeError` and the client gets a 500. The contract says 404. | POST punch-in with `emp_code: "EMP9999"`. | Return 404 when the employee is not found. |

| 11 | `punch_in`, time handling | `datetime.fromtimestamp` uses the machine's local zone and gives a naive datetime. `date` comes from that local time, not from IST (R1). There is no overnight-shift rule and microseconds are not truncated. `if body.punched_at` is false for 0. | Run on a UTC machine, or punch in at 23:30 IST. The date is wrong. Overnight punch-in at 01:30 lands on the wrong date. | UTC-aware conversion, `attendance_date()` using IST plus the overnight rule, truncation to whole seconds, `is not None` check. |

| 12 | `punch_in`, check then insert | Same race as #7: concurrent punch-ins for the same day can both be created. | Two simultaneous identical punch-ins. | Unique index on `(emp_code, date)` created at startup, and `DuplicateKeyError` mapped to 409. |

| 13 | `punch_in` and `list_attendance`, response shape | Responses leak `id` (the contract says no ids), return `punch_in` and `punch_out` as datetimes instead of epoch ms, and legacy records without `half_day` or `history` come back without them. | Compare the response with `AttendanceRecord`. | `record_out()` builds the response with ms times and defaults for missing fields, and converts history entries too. |

| 14 | `compute_late_minutes` | Builds the shift start from the punch-in's own date and clock, so it is wrong when the datetime is UTC. It truncates to whole minutes before comparing, so 09:40:01 counts as exactly 10 and is not late (R2 says strictly more than 10:00). | Sample record EMP0003 (10:05 IST, expected 35) returned 0 with a UTC datetime. 09:40:01 returned 0, expected 10. | Shift start is built in IST on the record's attendance date, compared in seconds (`> 600`), and returns floored minutes since shift start. |

| 15 | `compute_work_hours` | Python `round()` on a float is not half-up (R4). | 450 seconds gave 0.12 (expected 0.13). 9630 seconds gave 2.67 (expected 2.68). | `Decimal` from integer seconds with `ROUND_HALF_UP`. |

| 16 | `compute_overtime` | Has no 30-minute minimum (R3). For overnight shifts it puts shift end on the record date instead of the next day. | A punch-out 10 minutes after shift end returned 10 (expected 0). Sample overnight record EMP0005 returned 1480 (expected 40). | Shift end moves to the next day for overnight shifts, and minutes count only when `>= 30`. |

| 17 | `list_attendance`, query | Loads the whole collection into memory, sorts in Python, and sorts only by `date`. The contract says do not load the collection into memory, sort by `date` descending then `emp_code` ascending, and stay fast on about 100,000 records. | Code reading, and slow responses on large data. | Sort, skip and limit in MongoDB, `count_documents` for `total`, and an index on `(date, emp_code)`. |

| 18 | `list_attendance`, parameters | `page=0` slices backwards, `status` accepts anything, dates are plain strings, and `date_from > date_to` is accepted. | `?page=0`, `?status=banana`, `?date_from=2026-07-08&date_to=2026-07-07` all returned 200. | `Query` constraints, `Status` literal, `date` types, and 422 when `date_from > date_to`. |

| 19 | `health` | Always returns ok, even if MongoDB is down. The contract says 200 only if MongoDB answers a ping, otherwise 503. | Stop the Mongo container and call `/health`. | Ping MongoDB and return 503 if it fails. |

# ------------------------------------------------------------- 


Also note anything you looked at and decided was **not** a defect, and why.

1. {"_id": 0} in list_employees: correct. It keeps _id internal, as the contract requires.

2. The exact, case-sensitive department filter: matches the contract.

3. joined_on stored as a plain YYYY-MM-DD string: matches DATA_MODEL.md.
