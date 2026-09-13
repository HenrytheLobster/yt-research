@echo off
cd /d C:\Users\naylo\automation\yt-research

echo Running YouTube Research Engine (full overnight pipeline)...
C:\Users\naylo\AppData\Local\Programs\Python\Python313\python.exe streaming_overnight_run.py --hours 18 --discover-max 40 --process-max 2000 --collect-workers 3 --triage-workers 6 --extract-workers 6 --extract-model grok --fresh

echo.
echo Done.
pause
