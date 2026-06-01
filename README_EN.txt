OKX Trading Signal Analysis System - User Guide
================================

1. Install dependencies (first time use):
   pip install pandas numpy PySocks pywebview

2. Run the program:
   python trading_signal.py

3. Optional parameters:
   --instruments BTC ETH XAU     (specify instruments, default all 6)
   --refresh                     (force refresh from API, ignore local cache)

4. First run will automatically create a local cache database, subsequent runs will be faster

5. After startup, a desktop window will pop up, no browser needed
