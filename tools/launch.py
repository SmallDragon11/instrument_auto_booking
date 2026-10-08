"""PyInstaller 的進入點（打包成 ExperimentPlanner.exe）。"""
from instrument_booking.app.main import main

raise SystemExit(main())
