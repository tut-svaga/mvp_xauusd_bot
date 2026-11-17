RETAIL DATASET
==============

Main file: us_core_retail_sales.csv

One row = one US Core Retail Sales m/m release.
The headline Retail Sales m/m values are attached to the same row as companion
information because both indicators are published together. They must not be treated
as two independent price reactions.

Date/time policy
----------------
* Release date is taken from the explicit date field when present; otherwise from DateTime.
* Scheduled time is rebuilt as 08:30 America/New_York.
* Python zoneinfo applies historical New York daylight-saving rules automatically.
* UTC and Europe/Chisinau timestamps are rebuilt from that schedule.
* Original datetime/date/time fields are kept in the output for audit.
* For 21-column scraper exports, the separate date/time columns are treated as
  Europe/Chisinau local time. The supplied +00:00 marker is not trusted for that format.

Important columns
-----------------
release_date                calendar release date
release_time_et             official release time in New York
scheduled_datetime_utc      rebuilt scheduled UTC timestamp
scheduled_datetime_chisinau  rebuilt scheduled Moldova local timestamp
raw_datetime_new_york        source clock converted to New York, if available
raw_datetime_utc             source clock converted to UTC, if available
actual/forecast/previous     original text values from the calendar
actual_numeric/...           numeric versions (for example 0.4% -> 0.4)
surprise                    actual_numeric - forecast_numeric
surprise_sign               beat / miss / inline
headline_retail_*           regular Retail Sales m/m published with Core Retail

date_check                  internal consistency of duplicate date fields
time_check                  comparison of raw time against 08:30 New York
provided_datetime_utc_check comparison against precomputed datetime_utc, if supplied
source_value_conflicts      conflicting values found across input files

This script does not alter or delete source files and does not make trading forecasts.
